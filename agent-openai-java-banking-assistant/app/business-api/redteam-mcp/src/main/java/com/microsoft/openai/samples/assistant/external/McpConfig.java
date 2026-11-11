package com.microsoft.openai.samples.assistant.external;

import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class McpConfig {

    @Bean
    public ToolCallbackProvider externalTools(ExternalMcpService externalMcpService) {
        return MethodToolCallbackProvider.builder().toolObjects(externalMcpService).build();
    }
}
