// Copyright (c) Microsoft. All rights reserved.
package com.microsoft.openai.samples.assistant.config;


import dev.langchain4j.model.chat.ChatLanguageModel;
import dev.langchain4j.model.openai.OpenAiChatModel;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class Langchain4JConfiguration {

    @Value("${openai.base-url:https://api.openai.com/v1}")
    private String openAiBaseUrl;

    @Value("${openai.api-key}")
    private String openAiApiKey;

    @Value("${openai.model}")
    private String openAiModel;

    @Bean
    public ChatLanguageModel chatLanguageModel() {

        return OpenAiChatModel.builder()
                .baseUrl(openAiBaseUrl)
                .apiKey(openAiApiKey)
                .modelName(openAiModel)
                .temperature(0.3)
                .logRequests(true)
                .logResponses(true)
                .build();
    }


}
