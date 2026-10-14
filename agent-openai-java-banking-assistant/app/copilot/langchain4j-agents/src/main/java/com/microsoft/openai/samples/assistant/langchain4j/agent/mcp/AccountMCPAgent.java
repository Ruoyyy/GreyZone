package com.microsoft.openai.samples.assistant.langchain4j.agent.mcp;

import com.microsoft.langchain4j.agent.AgentMetadata;
import com.microsoft.langchain4j.agent.mcp.MCPProtocolType;
import com.microsoft.langchain4j.agent.mcp.MCPServerMetadata;
import com.microsoft.langchain4j.agent.mcp.MCPToolAgent;
import dev.langchain4j.model.chat.ChatLanguageModel;
import dev.langchain4j.model.input.PromptTemplate;

import java.util.List;
import java.util.Map;

public class AccountMCPAgent extends MCPToolAgent {

    private static final String ACCOUNT_AGENT_SYSTEM_MESSAGE = """
         you are a personal financial advisor who help the user to retrieve information about their bank accounts.
         Use html list or table to display the account information.
         Always use the below logged user details to retrieve account info:
         '{{loggedUserName}}'
         Only serve the logged user and their own accounts. Never retrieve or reveal any other user's information (accounts, balances, transactions, payment methods or beneficiaries). If the request refers to another user, or to an account id that does not belong to the logged user, politely refuse and do not call any tool with that value.
        """;

    private static final PromptTemplate PROMPT_TEMPLATE = PromptTemplate.from(ACCOUNT_AGENT_SYSTEM_MESSAGE);

    public AccountMCPAgent(ChatLanguageModel chatModel, String accountMCPServerUrl) {
        super(chatModel, List.of(new MCPServerMetadata("account",  accountMCPServerUrl, MCPProtocolType.SSE)));
    }

    @Override
    public String getName() {
        return "AccountAgent";
    }

    @Override
    public AgentMetadata getMetadata() {
        return new AgentMetadata(
            "Personal financial advisor for retrieving bank account information.",
            List.of("RetrieveAccountInfo", "DisplayAccountDetails")
        );
    }

    @Override
    protected String getSystemMessage(String loggedUserName) {
        return PROMPT_TEMPLATE.apply(Map.of("loggedUserName", loggedUserName)).text();
    }

}
