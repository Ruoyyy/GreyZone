// Copyright (c) Microsoft. All rights reserved.
package com.microsoft.openai.samples.assistant.config;


import dev.langchain4j.model.chat.ChatLanguageModel;
import dev.langchain4j.model.openai.OpenAiChatModel;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class Langchain4JConfiguration {

    @Value("${deepseek.base-url:https://api.deepseek.com}")
    private String deepSeekBaseUrl;

    @Value("${deepseek.api-key}")
    private String deepSeekApiKey;

    @Value("${deepseek.model:deepseek-chat}")
    private String deepSeekModel;

    @Bean
    public ChatLanguageModel chatLanguageModel() {

        return OpenAiChatModel.builder()
                .baseUrl(deepSeekBaseUrl)
                .apiKey(deepSeekApiKey)
                .modelName(deepSeekModel)
                .temperature(0.3)
                .logRequests(true)
                .logResponses(true)
                .build();
    }


}
