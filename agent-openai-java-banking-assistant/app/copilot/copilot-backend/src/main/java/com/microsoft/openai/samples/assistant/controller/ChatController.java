// Copyright (c) Microsoft. All rights reserved.
package com.microsoft.openai.samples.assistant.controller;


import com.microsoft.openai.samples.assistant.langchain4j.agent.SupervisorAgent;
import com.microsoft.openai.samples.assistant.persistence.ConversationMessage;
import com.microsoft.openai.samples.assistant.persistence.ConversationMessageRepository;
import com.microsoft.openai.samples.assistant.persistence.AppUserRepository;
import dev.langchain4j.data.message.AiMessage;
import dev.langchain4j.data.message.ChatMessage;
import dev.langchain4j.data.message.UserMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

@RestController
public class ChatController {

    private static final Logger LOGGER = LoggerFactory.getLogger(ChatController.class);
    private final SupervisorAgent supervisorAgent;
    private final AppUserRepository appUserRepository;
    private final ConversationMessageRepository conversationRepository;

    public ChatController(SupervisorAgent supervisorAgent, AppUserRepository appUserRepository, ConversationMessageRepository conversationRepository) {
        this.supervisorAgent = supervisorAgent;
        this.appUserRepository = appUserRepository;
        this.conversationRepository = conversationRepository;
    }

    @PostMapping(value = "/api/chat", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ChatResponse> openAIAsk(@RequestBody ChatAppRequest chatRequest) {
        if (chatRequest.stream()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Requested a content-type of application/json however also requested streaming."
                            + " Please use a content-type of application/ndjson");
        }

        if (chatRequest.messages() == null || chatRequest.messages().isEmpty()) {
            LOGGER.warn("messages cannot be null or empty in Chat request");
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(null);
        }

        String user = chatRequest.user();
        if (user == null || user.isBlank() || !appUserRepository.existsById(user)) {
            LOGGER.warn("Unknown or missing user in Chat request: [{}]", user);
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(null);
        }

        // The new user turn is the last message in the request; server owns the rest of the history.
        ResponseMessage last = chatRequest.messages().get(chatRequest.messages().size() - 1);
        String newContent = last.content();
        List<String> attachments = last.attachments();

        // Load this user's persisted history (isolated by user) as the source of truth.
        List<ChatMessage> chatHistory = new ArrayList<>();
        conversationRepository.findByUserIdOrderByIdAsc(user).forEach(m -> {
            if ("user".equals(m.getRole())) {
                chatHistory.add(UserMessage.from(m.getContent()));
            } else if ("assistant".equals(m.getRole())) {
                chatHistory.add(AiMessage.from(m.getContent()));
            }
        });

        // Append the new user message (attachments are inlined like the previous behaviour).
        String withAttachments = (attachments == null || attachments.isEmpty())
                ? newContent : newContent + " " + attachments.toString();
        chatHistory.add(UserMessage.from(withAttachments));

        // Persist the user turn before invoking.
        conversationRepository.save(new ConversationMessage(user, "user", newContent,
                (attachments == null || attachments.isEmpty()) ? null : String.join(",", attachments)));

        LOGGER.debug("Processing chat conversation for user [{}]..", user);
        List<ChatMessage> agentsResponse = supervisorAgent.invoke(chatHistory, user);
        AiMessage generatedResponse = (AiMessage) agentsResponse.get(agentsResponse.size() - 1);

        // Persist the assistant turn.
        conversationRepository.save(new ConversationMessage(user, "assistant", generatedResponse.text(), null));

        return ResponseEntity.ok(ChatResponse.buildChatResponse(generatedResponse));
    }

    @GetMapping(value = "/api/chat/history", produces = MediaType.APPLICATION_JSON_VALUE)
    public List<ResponseMessage> history(@RequestParam String user) {
        return conversationRepository.findByUserIdOrderByIdAsc(user).stream()
                .map(m -> new ResponseMessage(
                        m.getContent(),
                        m.getRole(),
                        (m.getAttachments() == null || m.getAttachments().isBlank())
                                ? List.of() : Arrays.asList(m.getAttachments().split(","))))
                .collect(Collectors.toList());
    }
}
