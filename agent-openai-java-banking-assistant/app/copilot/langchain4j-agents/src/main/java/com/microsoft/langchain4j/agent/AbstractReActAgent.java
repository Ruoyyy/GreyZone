package com.microsoft.langchain4j.agent;

import dev.langchain4j.agent.tool.ToolSpecification;
import dev.langchain4j.data.message.ChatMessage;
import dev.langchain4j.data.message.SystemMessage;
import dev.langchain4j.memory.ChatMemory;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;
import dev.langchain4j.agent.tool.ToolExecutionRequest;
import dev.langchain4j.data.message.ToolExecutionResultMessage;
import dev.langchain4j.model.chat.ChatLanguageModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.request.ChatRequestParameters;
import dev.langchain4j.service.tool.ToolExecutor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

public abstract class AbstractReActAgent implements Agent {

    private static final Logger LOGGER = LoggerFactory.getLogger(AbstractReActAgent.class);

    // Hard bound on model/tool round-trips per invocation so a model stuck in a
    // tool-calling loop (e.g. after a prompt injection) cannot run forever.
    private static final int MAX_TOOL_ITERATIONS = 10;

    protected final ChatLanguageModel chatModel;

    protected AbstractReActAgent(ChatLanguageModel chatModel) {
        if (chatModel == null) {
            throw new IllegalArgumentException("chatModel cannot be null");
        }
        this.chatModel = chatModel;
    }

    @Override
    public List<ChatMessage> invoke(List<ChatMessage> chatHistory, String loggedUserName) throws AgentExecutionException {
        LOGGER.info("------------- {} -------------", this.getName());

        try {
            var internalChatMemory = buildInternalChat(chatHistory, loggedUserName);

            ChatRequestParameters parameters = ChatRequestParameters.builder()
                .toolSpecifications(getToolSpecifications())
                .build();

            ChatRequest request = ChatRequest.builder()
                .messages(internalChatMemory.messages())
                .parameters(parameters)
                .build();

            var aiMessage = chatModel.chat(request).aiMessage();

            // ReAct planning with tools
            int toolIterations = 0;
            while (aiMessage != null && aiMessage.hasToolExecutionRequests()) {
                toolIterations++;
                if (toolIterations > MAX_TOOL_ITERATIONS) {
                    LOGGER.warn("Agent [{}] exceeded {} tool iterations, forcing a final answer without tools",
                        this.getName(), MAX_TOOL_ITERATIONS);
                    // Drop the pending tool request and ask for a plain answer: the message
                    // history stays consistent because the request was never added to memory.
                    ChatRequest finalAnswerRequest = ChatRequest.builder()
                        .messages(internalChatMemory.messages())
                        .build();
                    aiMessage = chatModel.chat(finalAnswerRequest).aiMessage();
                    break;
                }

                List<ToolExecutionResultMessage> toolExecutionResultMessages = executeToolRequests(aiMessage.toolExecutionRequests());

                internalChatMemory.add(aiMessage);
                toolExecutionResultMessages.forEach(internalChatMemory::add);

                ChatRequest toolExecutionResultResponseRequest = ChatRequest.builder()
                    .messages(internalChatMemory.messages())
                    .parameters(parameters)
                    .build();

                aiMessage = chatModel.chat(toolExecutionResultResponseRequest).aiMessage();
            }

            LOGGER.info("Agent response: {}", aiMessage.text());

            // add last ai message to agent internal memory
            internalChatMemory.add(aiMessage);
            return buildResponse(chatHistory, internalChatMemory);
        } catch (Exception e) {
            throw new AgentExecutionException("Error during agent [%s] invocation".formatted(this.getName()), e);
        }
    }

    protected List<ChatMessage> buildResponse(List<ChatMessage> chatHistory, ChatMemory internalChatMemory) {
        return internalChatMemory.messages()
                .stream()
                .filter(m -> !(m instanceof SystemMessage))
                .collect(Collectors.toList());
    }

    protected List<ToolExecutionResultMessage> executeToolRequests(List<ToolExecutionRequest> toolExecutionRequests) {
        List<ToolExecutionResultMessage> toolExecutionResultMessages = new ArrayList<>();
        for (ToolExecutionRequest toolExecutionRequest : toolExecutionRequests) {
            var toolExecutor = getToolExecutor(toolExecutionRequest.name());
            LOGGER.info("Executing {} with params {}", toolExecutionRequest.name(), toolExecutionRequest.arguments());
            String result;
            try {
                result = toolExecutor.execute(toolExecutionRequest, null);
                LOGGER.info("Response from {}: {}", toolExecutionRequest.name(), result);
                if (result == null || result.isEmpty()) {
                    // Only reachable when the tool completed without error and returned
                    // nothing (void tools): report the fact instead of claiming success.
                    LOGGER.warn("Tool {} returned an empty result", toolExecutionRequest.name());
                    result = "Tool executed successfully but returned no output";
                }
            } catch (Exception e) {
                LOGGER.error("Tool {} execution failed", toolExecutionRequest.name(), e);
                result = "Tool execution failed: " + e;
            }
            toolExecutionResultMessages.add(ToolExecutionResultMessage.from(toolExecutionRequest, result));
        }
        return toolExecutionResultMessages;
    }

    protected ChatMemory buildInternalChat(List<ChatMessage> chatHistory, String loggedUserName) {
        var internalChatMemory = MessageWindowChatMemory.builder()
            .id("default")
            .maxMessages(20)
            .build();

        internalChatMemory.add(SystemMessage.from(getSystemMessage(loggedUserName)));
        chatHistory.forEach(internalChatMemory::add);
        return internalChatMemory;
    }

    protected abstract String getSystemMessage(String loggedUserName);

    protected abstract List<ToolSpecification> getToolSpecifications();

    protected abstract ToolExecutor getToolExecutor(String toolName);
}