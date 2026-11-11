package com.microsoft.openai.samples.assistant.external;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.microsoft.openai.samples.assistant.external.model.Invoice;
import com.microsoft.openai.samples.assistant.external.model.Payment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.List;

/**
 * Thin gateway that forwards external MCP calls to the payment service's REST API,
 * so data lands in the same stores the workspace agent reads from. It also forwards
 * chat prompts to the copilot backend, always as the fixed user 'alice'.
 */
@Service
public class ExternalIntegrationService {

    private static final Logger logger = LoggerFactory.getLogger(ExternalIntegrationService.class);
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String ATTACK_USER = "alice";

    private final WebClient webClient;
    private final String paymentApiUrl;
    private final String copilotApiUrl;
    private volatile String lastReply;

    public ExternalIntegrationService(WebClient.Builder webClientBuilder,
                                      @Value("${payment.api.url}") String paymentApiUrl,
                                      @Value("${copilot.api.url}") String copilotApiUrl) {
        this.webClient = webClientBuilder.build();
        this.paymentApiUrl = paymentApiUrl;
        this.copilotApiUrl = copilotApiUrl;
    }

    public void receivePayment(Payment payment) {
        logger.info("Forwarding incoming payment to payment service: {}", payment);
        webClient.post()
                .uri(paymentApiUrl + "/payments/incoming")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(payment)
                .retrieve()
                .toBodilessEntity()
                .block();
    }

    public void submitInvoice(Invoice invoice) {
        logger.info("Forwarding invoice to payment service: {}", invoice.getInvoiceId());
        webClient.post()
                .uri(paymentApiUrl + "/invoices")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(invoice)
                .retrieve()
                .toBodilessEntity()
                .block();
    }

    /**
     * Chat with the workspace copilot as the fixed 'alice' user and return the agent's reply.
     * Consecutive calls continue the same server-side conversation for that user.
     */
    public String sendPrompt(String prompt) {
        logger.info("Forwarding prompt to copilot as [{}]: {}", ATTACK_USER, prompt);
        String response = webClient.post()
                .uri(copilotApiUrl + "/api/chat")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(new ChatRequest(List.of(new ChatTurn(prompt, "user", null)), false, ATTACK_USER))
                .retrieve()
                .bodyToMono(String.class)
                .block();

        String reply = extractReply(response);
        this.lastReply = reply;
        logger.info("Copilot reply for [{}]: {}", ATTACK_USER, reply);
        return reply;
    }

    /** Re-read the last copilot reply without sending a new prompt. */
    public String receiveReply() {
        return lastReply;
    }

    private String extractReply(String response) {
        try {
            JsonNode content = JSON.readTree(response).path("choices").path(0).path("message").path("content");
            return content.isMissingNode() ? response : content.asText();
        } catch (Exception e) {
            return response;
        }
    }

    private record ChatTurn(String content, String role, List<String> attachments) {}

    private record ChatRequest(List<ChatTurn> messages, boolean stream, String user) {}
}
