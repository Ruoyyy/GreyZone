package com.microsoft.openai.samples.assistant.langchain4j.agent;


import com.microsoft.langchain4j.agent.Agent;
import com.microsoft.langchain4j.agent.AgentExecutionException;
import com.microsoft.langchain4j.agent.AgentMetadata;
import dev.langchain4j.data.message.AiMessage;
import dev.langchain4j.data.message.ChatMessage;
import dev.langchain4j.data.message.ToolExecutionResultMessage;
import dev.langchain4j.memory.ChatMemory;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;
import dev.langchain4j.model.chat.ChatLanguageModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.input.Prompt;
import dev.langchain4j.model.input.PromptTemplate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

public class SupervisorAgent {

    private final Logger LOGGER = LoggerFactory.getLogger(SupervisorAgent.class);
    private final ChatLanguageModel chatLanguageModel;
    private final List<Agent> agents;
    private final Map<String, AgentMetadata> agentsMetadata;
    private final Prompt agentPrompt;
    //When false only detect the next agent but doesn't route to it. It will answer with the agent name.
    private Boolean routing = true;

   private final String SUPERVISOR_AGENT_SINGLETURN_SYSTEM_MESSAGE = """
        You are a banking customer support agent triaging conversation and select the best agent name that can solve the customer need.
        Use the below list of agents metadata to select the best one for the customer request:
        {{agentsMetadata}}
        Base your decision on the customer's LAST message; use earlier messages only as context for follow-ups.
        Answer only with the agent name.
        if you are not able to select an agent answer with none.
        """;

    public SupervisorAgent(ChatLanguageModel chatLanguageModel, List<Agent> agents, Boolean routing) {
        this.chatLanguageModel = chatLanguageModel;
        this.agents = agents;
        this.routing = routing;

        this.agentsMetadata = agents.stream()
                .collect(Collectors.toMap(Agent::getName, Agent::getMetadata));

        PromptTemplate promptTemplate = PromptTemplate.from(SUPERVISOR_AGENT_SINGLETURN_SYSTEM_MESSAGE);
        agentPrompt =promptTemplate.apply(Map.of("agentsMetadata", this.agentsMetadata));

    }
    public SupervisorAgent(ChatLanguageModel chatLanguageModel, List<Agent> agents) {
       this(chatLanguageModel, agents, true);
    }


    public List<ChatMessage> invoke(List<ChatMessage> chatHistory, String loggedUserName) {
        LOGGER.info("------------- SupervisorAgent -------------");

        var internalChatMemory = buildInternalChat(chatHistory);

        ChatRequest request = ChatRequest.builder()
                .messages(internalChatMemory.messages())
                .build();

        AiMessage aiMessage = chatLanguageModel.chat(request).aiMessage();
        String nextAgent = aiMessage.text();
        LOGGER.info("Supervisor Agent handoff to [{}]", nextAgent);

     if (routing) {
           return singleTurnRouting(nextAgent, chatHistory, loggedUserName);
        }

     return new ArrayList<>();
    }


    protected List<ChatMessage> singleTurnRouting(String nextAgent, List<ChatMessage> chatHistory, String loggedUserName) {
        if("none".equalsIgnoreCase(nextAgent)){
            LOGGER.info("Gracefully handle clarification.. ");
            AiMessage clarificationMessage = AiMessage.builder().
                    text(" I'm not sure about your request. Can you please clarify?")
                    .build();
            chatHistory.add(clarificationMessage);
            return chatHistory;
        }

        var agent = agents.stream()
                .filter(a -> a.getName().equals(nextAgent))
                .findFirst();

        if (agent.isEmpty()) {
            // The routing model returned something that is not a known agent name; clarify instead of failing.
            LOGGER.info("No matching agent for [{}]; asking for clarification.", nextAgent);
            AiMessage clarificationMessage = AiMessage.builder()
                    .text(" I'm not sure about your request. Can you please clarify?")
                    .build();
            chatHistory.add(clarificationMessage);
            return chatHistory;
        }

        return agent.get().invoke(chatHistory, loggedUserName);
    }



    private ChatMemory buildInternalChat(List<ChatMessage> chatHistory) {
        //build a new chat memory to preserve order of messages otherwise the model hallucinate.
        var internalChatMemory = MessageWindowChatMemory.builder()
                .id("default")
                .maxMessages(20)
                .build();

        internalChatMemory.add(dev.langchain4j.data.message.SystemMessage.from(agentPrompt.text()));
        // Route using only the user turns: assistant answers/clarifications accumulated in a long
        // persisted history otherwise derail the routing model (it may echo an answer instead of an
        // agent name). User turns preserve intent continuity for multi-turn flows (e.g. "use my Visa").
        chatHistory.stream()
                .filter(chatMessage -> chatMessage instanceof dev.langchain4j.data.message.UserMessage)
                .forEach(internalChatMemory::add);
        return internalChatMemory;
    }
}
