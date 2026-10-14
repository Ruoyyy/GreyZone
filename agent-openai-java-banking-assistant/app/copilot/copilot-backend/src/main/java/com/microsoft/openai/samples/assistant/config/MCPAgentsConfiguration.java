// Copyright (c) Microsoft. All rights reserved.
package com.microsoft.openai.samples.assistant.config;

import com.microsoft.openai.samples.assistant.invoice.DocumentIntelligenceInvoiceScanHelper;
import com.microsoft.openai.samples.assistant.langchain4j.agent.SupervisorAgent;
import com.microsoft.openai.samples.assistant.langchain4j.agent.mcp.AccountMCPAgent;
import com.microsoft.openai.samples.assistant.langchain4j.agent.mcp.PaymentMCPAgent;
import com.microsoft.openai.samples.assistant.langchain4j.agent.mcp.TransactionHistoryMCPAgent;
import dev.langchain4j.model.chat.ChatLanguageModel;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;

@Configuration
public class MCPAgentsConfiguration {
    @Value("${transactions.api.url}") String transactionsMCPServerUrl;
    @Value("${accounts.api.url}") String accountsMCPServerUrl;
    @Value("${payments.api.url}") String paymentsMCPServerUrl;

    private final ChatLanguageModel chatLanguageModel;
    private final DocumentIntelligenceInvoiceScanHelper documentIntelligenceInvoiceScanHelper;

    public MCPAgentsConfiguration(ChatLanguageModel chatLanguageModel, DocumentIntelligenceInvoiceScanHelper documentIntelligenceInvoiceScanHelper) {
        this.chatLanguageModel = chatLanguageModel;
        this.documentIntelligenceInvoiceScanHelper = documentIntelligenceInvoiceScanHelper;
    }
    @Bean
    public AccountMCPAgent accountMCPAgent() {
        return new AccountMCPAgent(chatLanguageModel, accountsMCPServerUrl);
    }

    @Bean
    public TransactionHistoryMCPAgent transactionHistoryMCPAgent() {
        return new TransactionHistoryMCPAgent(chatLanguageModel, transactionsMCPServerUrl, accountsMCPServerUrl);
    }

    @Bean
    public PaymentMCPAgent paymentMCPAgent() {
        return new PaymentMCPAgent(chatLanguageModel, documentIntelligenceInvoiceScanHelper, transactionsMCPServerUrl, accountsMCPServerUrl, paymentsMCPServerUrl);
    }

    @Bean
    public SupervisorAgent supervisorAgent(ChatLanguageModel chatLanguageModel){
        return new SupervisorAgent(chatLanguageModel,
                List.of(accountMCPAgent(),
                        transactionHistoryMCPAgent(),
                        paymentMCPAgent()));

    }

}
