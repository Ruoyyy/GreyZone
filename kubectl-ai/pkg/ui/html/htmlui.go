// Copyright 2025 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package html

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/GoogleCloudPlatform/kubectl-ai/pkg/agent"
	"github.com/GoogleCloudPlatform/kubectl-ai/pkg/api"
	"github.com/GoogleCloudPlatform/kubectl-ai/pkg/journal"
	"github.com/GoogleCloudPlatform/kubectl-ai/pkg/sessions"
	"github.com/GoogleCloudPlatform/kubectl-ai/pkg/ui"
	"github.com/charmbracelet/glamour"
	"golang.org/x/sync/errgroup"
	"k8s.io/klog/v2"
)

// Broadcaster manages a set of clients for Server-Sent Events.
type Broadcaster struct {
	clients   map[chan []byte]bool
	newClient chan chan []byte
	delClient chan chan []byte
	messages  chan []byte
	mu        sync.Mutex
}

// NewBroadcaster creates a new Broadcaster instance.
func NewBroadcaster() *Broadcaster {
	b := &Broadcaster{
		clients:   make(map[chan []byte]bool),
		newClient: make(chan (chan []byte)),
		delClient: make(chan (chan []byte)),
		messages:  make(chan []byte, 10),
	}
	return b
}

// Run starts the broadcaster's event loop.
func (b *Broadcaster) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case client := <-b.newClient:
			b.mu.Lock()
			b.clients[client] = true
			b.mu.Unlock()
		case client := <-b.delClient:
			b.mu.Lock()
			delete(b.clients, client)
			close(client)
			b.mu.Unlock()
		case msg := <-b.messages:
			b.mu.Lock()
			for client := range b.clients {
				select {
				case client <- msg:
				default:
					klog.Warning("SSE client buffer full, dropping message.")
				}
			}
			b.mu.Unlock()
		}
	}
}

// Broadcast sends a message to all connected clients.
func (b *Broadcaster) Broadcast(msg []byte) {
	b.messages <- msg
}

type HTMLUserInterface struct {
	httpServer         *http.Server
	httpServerListener net.Listener

	manager         *agent.AgentManager
	sessionManager  *sessions.SessionManager
	journal         journal.Recorder
	defaultModel    string
	defaultProvider string

	markdownRenderer *glamour.TermRenderer
	broadcasters     map[string]*Broadcaster
	broadcastersMu   sync.Mutex

	broadcasterCancels map[string]context.CancelFunc
	baseCtx            context.Context
}

var _ ui.UI = &HTMLUserInterface{}

func NewHTMLUserInterface(manager *agent.AgentManager, sessionManager *sessions.SessionManager, defaultModel, defaultProvider string, listenAddress string, journal journal.Recorder) (*HTMLUserInterface, error) {
	mux := http.NewServeMux()

	u := &HTMLUserInterface{
		manager:            manager,
		sessionManager:     sessionManager,
		defaultModel:       defaultModel,
		defaultProvider:    defaultProvider,
		journal:            journal,
		broadcasters:       make(map[string]*Broadcaster),
		broadcasterCancels: make(map[string]context.CancelFunc),
	}

	// Register callback to listen to new agents
	manager.SetAgentCreatedCallback(func(a *agent.Agent) {
		u.ensureAgentListener(a)
	})

	httpServer := &http.Server{
		Addr:    listenAddress,
		Handler: mux,
	}

	mux.HandleFunc("GET /", u.serveIndex)
	mux.HandleFunc("GET /api/sessions", u.handleListSessions)
	mux.HandleFunc("POST /api/sessions", u.handleCreateSession)
	mux.HandleFunc("POST /api/sessions/{id}/rename", u.handleRenameSession)
	mux.HandleFunc("DELETE /api/sessions/{id}", u.handleDeleteSession)
	mux.HandleFunc("GET /api/sessions/{id}/stream", u.handleSessionStream)
	mux.HandleFunc("POST /api/sessions/{id}/send-message", u.handlePOSTSendMessage)
	mux.HandleFunc("POST /api/sessions/{id}/choose-option", u.handlePOSTChooseOption)

	httpServerListener, err := net.Listen("tcp", listenAddress)
	if err != nil {
		return nil, fmt.Errorf("starting http server network listener: %w", err)
	}
	endpoint := httpServerListener.Addr()
	u.httpServerListener = httpServerListener
	u.httpServer = httpServer

	fmt.Fprintf(os.Stdout, "listening on http://%s\n", endpoint)

	mdRenderer, err := glamour.NewTermRenderer(
		glamour.WithAutoStyle(),
		glamour.WithPreservedNewLines(),
		glamour.WithEmoji(),
	)
	if err != nil {
		return nil, fmt.Errorf("error initializing the markdown renderer: %w", err)
	}
	u.markdownRenderer = mdRenderer

	return u, nil
}

func (u *HTMLUserInterface) Run(ctx context.Context) error {
	g, gctx := errgroup.WithContext(ctx)

	u.baseCtx = gctx

	g.Go(func() error {
		if err := u.httpServer.Serve(u.httpServerListener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			return fmt.Errorf("error running http server: %w", err)
		}
		return nil
	})

	g.Go(func() error {
		<-gctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := u.httpServer.Shutdown(shutdownCtx); err != nil {
			klog.Errorf("HTTP server shutdown error: %v", err)
		}
		return nil
	})

	return g.Wait()
}

//go:embed index.html
var indexHTML []byte

func (u *HTMLUserInterface) serveIndex(w http.ResponseWriter, req *http.Request) {
	w.Header().Set("Content-Type", "text/html")
	w.Write(indexHTML)
}

func (u *HTMLUserInterface) handleSessionStream(w http.ResponseWriter, req *http.Request) {
	ctx := req.Context()
	log := klog.FromContext(ctx)

	id := req.PathValue("id")
	if id == "" {
		http.Error(w, "missing session id", http.StatusBadRequest)
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported!", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	clientChan := make(chan []byte, 10)
	broadcaster := u.getBroadcaster(id)
	broadcaster.newClient <- clientChan
	defer func() {
		broadcaster.delClient <- clientChan
	}()

	log.Info("SSE client connected", "sessionID", id)

	agent, err := u.manager.GetAgent(ctx, id)
	var initialData []byte
	if err != nil {
		log.Error(err, "getting agent for session")
	} else {
		initialData, err = u.getSessionStateJSON(agent.Session)
	}

	if err != nil {
		log.Error(err, "getting initial state for SSE client")
	} else {
		fmt.Fprintf(w, "data: %s\n\n", initialData)
		flusher.Flush()
	}

	for {
		select {
		case <-ctx.Done():
			log.Info("SSE client disconnected")
			return
		case msg := <-clientChan:
			fmt.Fprintf(w, "data: %s\n\n", msg)
			flusher.Flush()
		}
	}
}

func (u *HTMLUserInterface) handleListSessions(w http.ResponseWriter, req *http.Request) {
	ctx := req.Context()
	log := klog.FromContext(ctx)

	sessionsList, err := u.manager.ListSessions()
	if err != nil {
		log.Error(err, "listing sessions")
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(sessionsList); err != nil {
		log.Error(err, "encoding sessions list")
	}
}

func (u *HTMLUserInterface) handleCreateSession(w http.ResponseWriter, req *http.Request) {
	ctx := req.Context()
	log := klog.FromContext(ctx)

	meta := sessions.Metadata{
		ModelID:    u.defaultModel,
		ProviderID: u.defaultProvider,
	}

	session, err := u.sessionManager.NewSession(meta)
	if err != nil {
		log.Error(err, "creating new session")
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Ensure agent is started/loaded (though mostly for side effect of starting if not started)
	if _, err := u.manager.GetAgent(ctx, session.ID); err != nil {
		log.Error(err, "starting agent for new session")
		// We don't fail the request here necessarily, but it's good to know.
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"id": session.ID})
}

func (u *HTMLUserInterface) handleRenameSession(w http.ResponseWriter, req *http.Request) {
	ctx := req.Context()
	log := klog.FromContext(ctx)

	id := req.PathValue("id")
	if id == "" {
		http.Error(w, "missing session id", http.StatusBadRequest)
		return
	}

	if err := req.ParseForm(); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	newName := req.FormValue("name")
	if newName == "" {
		http.Error(w, "missing name", http.StatusBadRequest)
		return
	}

	session, err := u.manager.FindSessionByID(id)
	if err != nil {
		http.Error(w, "session not found", http.StatusNotFound)
		return
	}

	session.Name = newName
	if err := u.manager.UpdateLastAccessed(session); err != nil { // UpdateLastAccessed also saves the session
		log.Error(err, "updating session")
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	if agent, err := u.manager.GetAgent(ctx, id); err == nil {
		agent.Session.Name = newName
		// Broadcast update
		if data, err := u.getSessionStateJSON(agent.Session); err == nil {
			u.getBroadcaster(id).Broadcast(data)
		}
	}

	w.WriteHeader(http.StatusOK)
}

func (u *HTMLUserInterface) handleDeleteSession(w http.ResponseWriter, req *http.Request) {
	ctx := req.Context()
	log := klog.FromContext(ctx)

	id := req.PathValue("id")
	if id == "" {
		http.Error(w, "missing session id", http.StatusBadRequest)
		return
	}

	if err := u.manager.DeleteSession(id); err != nil {
		log.Error(err, "deleting session")
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// If anyone was listening to this session, they should know it's gone.
	// We can close the broadcaster.
	u.broadcastersMu.Lock()
	if cancel, ok := u.broadcasterCancels[id]; ok {
		cancel()
		delete(u.broadcasterCancels, id)
	}
	delete(u.broadcasters, id)
	u.broadcastersMu.Unlock()

	w.WriteHeader(http.StatusOK)
}

func (u *HTMLUserInterface) handlePOSTSendMessage(w http.ResponseWriter, req *http.Request) {
	ctx := req.Context()
	log := klog.FromContext(ctx)

	id := req.PathValue("id")
	if id == "" {
		http.Error(w, "missing session id", http.StatusBadRequest)
		return
	}

	if err := req.ParseForm(); err != nil {
		log.Error(err, "parsing form")
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	q := req.FormValue("q")
	if q == "" {
		http.Error(w, "missing query", http.StatusBadRequest)
		return
	}

	// Get the agent for this session
	agent, err := u.manager.GetAgent(ctx, id)
	if err != nil {
		log.Error(err, "getting agent")
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Send the message to the agent
	agent.Input <- &api.UserInputResponse{Query: q}

	w.WriteHeader(http.StatusOK)
}

func (u *HTMLUserInterface) handlePOSTChooseOption(w http.ResponseWriter, req *http.Request) {
	ctx := req.Context()
	log := klog.FromContext(ctx)

	id := req.PathValue("id")
	if id == "" {
		http.Error(w, "missing session id", http.StatusBadRequest)
		return
	}

	if err := req.ParseForm(); err != nil {
		log.Error(err, "parsing form")
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	choice := req.FormValue("choice")
	if choice == "" {
		http.Error(w, "missing choice", http.StatusBadRequest)
		return
	}

	choiceIndex, err := strconv.Atoi(choice)
	if err != nil {
		http.Error(w, "invalid choice", http.StatusBadRequest)
		return
	}

	// Get the agent
	agent, err := u.manager.GetAgent(ctx, id)
	if err != nil {
		http.Error(w, "agent not found", http.StatusNotFound)
		return
	}

	// Send the choice to the agent
	agent.Input <- &api.UserChoiceResponse{Choice: choiceIndex}

	w.WriteHeader(http.StatusOK)
}

func (u *HTMLUserInterface) Close() error {
	var errs []error
	if u.httpServerListener != nil {
		if err := u.httpServerListener.Close(); err != nil {
			errs = append(errs, err)
		} else {
			u.httpServerListener = nil
		}
	}

	u.broadcastersMu.Lock()
	for id, cancel := range u.broadcasterCancels {
		cancel()
		delete(u.broadcasterCancels, id)
	}
	u.broadcasters = make(map[string]*Broadcaster)
	u.broadcastersMu.Unlock()

	return errors.Join(errs...)
}

func (u *HTMLUserInterface) ClearScreen() {
	// Not applicable for HTML UI
}

func (u *HTMLUserInterface) getSessionStateJSON(session *api.Session) ([]byte, error) {
	allMessages := session.AllMessages()
	// Create a copy of the messages to avoid race conditions
	var messages []*api.Message
	for _, message := range allMessages {
		if message.Type == api.MessageTypeUserInputRequest && message.Payload == ">>>" {
			continue
		}
		messages = append(messages, message)
	}

	agentState := session.AgentState

	data := map[string]interface{}{
		"messages":   messages,
		"agentState": agentState,
		"sessionId":  session.ID,
	}
	return json.Marshal(data)
}

func (u *HTMLUserInterface) getBroadcaster(sessionID string) *Broadcaster {
	u.broadcastersMu.Lock()
	defer u.broadcastersMu.Unlock()

	if b, ok := u.broadcasters[sessionID]; ok {
		return b
	}

	b := NewBroadcaster()
	u.broadcasters[sessionID] = b

	parent := u.baseCtx
	if parent == nil {
		parent = context.Background()
	}
	ctx, cancel := context.WithCancel(parent)
	u.broadcasterCancels[sessionID] = cancel

	// Start the broadcaster loop
	go b.Run(ctx)

	return b
}

func (u *HTMLUserInterface) ensureAgentListener(a *agent.Agent) {
	// Start a goroutine to listen to this agent's output
	go func() {
		for range a.Output {
			// Broadcast state
			if a.Session == nil {
				continue
			}

			data, err := u.getSessionStateJSON(a.Session)
			if err != nil {
				klog.Errorf("Error marshaling state for broadcast: %v", err)
				continue
			}

			b := u.getBroadcaster(a.Session.ID)
			b.Broadcast(data)
		}
	}()
}
