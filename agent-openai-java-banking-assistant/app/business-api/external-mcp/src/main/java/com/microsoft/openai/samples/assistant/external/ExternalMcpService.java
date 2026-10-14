package com.microsoft.openai.samples.assistant.external;

import com.microsoft.openai.samples.assistant.external.model.Invoice;
import com.microsoft.openai.samples.assistant.external.model.Payment;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.stereotype.Service;

/**
 * MCP tools exposed exclusively to external agents (this server is not connected by the workspace agent).
 */
@Service
public class ExternalMcpService {

    private final ExternalIntegrationService integrationService;

    public ExternalMcpService(ExternalIntegrationService integrationService) {
        this.integrationService = integrationService;
    }

    @Tool(description = "Record an incoming payment or transfer received from a third party into the account")
    public void receivePayment(Payment payment) {
        integrationService.receivePayment(payment);
    }

    @Tool(description = "Submit or register an invoice (bill) with its extracted fields so it can later be paid")
    public void submitInvoice(Invoice invoice) {
        integrationService.submitInvoice(invoice);
    }

    @Tool(description = "Send a chat message to the banking copilot agent as the user 'ruoyu' and return the agent's reply. Consecutive calls continue the same conversation.")
    public String sendPrompt(String prompt) {
        return integrationService.sendPrompt(prompt);
    }

    @Tool(description = "Return the last reply the banking copilot agent gave to the user 'ruoyu', without sending a new message")
    public String receiveReply() {
        return integrationService.receiveReply();
    }
}
