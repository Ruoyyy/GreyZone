package com.microsoft.openai.samples.assistant.langchain4j.agent.mcp;

import com.microsoft.langchain4j.agent.AgentMetadata;
import com.microsoft.langchain4j.agent.mcp.MCPProtocolType;
import com.microsoft.langchain4j.agent.mcp.MCPServerMetadata;
import com.microsoft.langchain4j.agent.mcp.MCPToolAgent;
import dev.langchain4j.model.chat.ChatLanguageModel;
import dev.langchain4j.model.input.PromptTemplate;

import java.util.List;
import java.util.Map;

public class TransactionHistoryMCPAgent extends MCPToolAgent {

    private static final String TRANSACTION_HISTORY_AGENT_SYSTEM_MESSAGE = """
         you are a personal financial advisor who help the user with their recurrent bill payments. To search about the payments history you need to know the payee name and the account id.
        If the user doesn't provide the payee name, search the last 10 transactions order by date.
        If the user want to search last transactions for a specific payee, ask to provide the payee name.
        Use html list or table to display the transaction information.
        Always use the below logged user details to retrieve account info:
        '{{loggedUserName}}'
        Only serve the logged user and their own accounts. Never retrieve or reveal any other user's information (accounts, balances, transactions, payment methods or beneficiaries). If the request refers to another user, or to an account id that does not belong to the logged user, politely refuse and do not call any tool with that value.
        Current timestamp:
        '{{currentDateTime}}'
        """;

    private static final PromptTemplate PROMPT_TEMPLATE = PromptTemplate.from(TRANSACTION_HISTORY_AGENT_SYSTEM_MESSAGE);

    public TransactionHistoryMCPAgent(ChatLanguageModel chatModel, String transactionMCPServerUrl, String accountMCPServerUrl) {
        super(chatModel, List.of(new MCPServerMetadata("transaction-history", transactionMCPServerUrl, MCPProtocolType.SSE),
                                 new MCPServerMetadata("account", accountMCPServerUrl, MCPProtocolType.SSE)));
    }

    @Override
    public String getName() {
        return "TransactionHistoryAgent";
    }

    @Override
    public AgentMetadata getMetadata() {
        return new AgentMetadata(
            "Personal financial advisor for retrieving transaction history information.",
            List.of("RetrieveTransactionHistory", "DisplayTransactionDetails")
        );
    }

    @Override
    protected String getSystemMessage(String loggedUserName) {
        var datetimeIso8601 = java.time.ZonedDateTime.now(java.time.ZoneId.of("UTC")).toInstant().toString();
        return PROMPT_TEMPLATE.apply(Map.of(
                "loggedUserName", loggedUserName,
                "currentDateTime", datetimeIso8601
        )).text();
    }

}
