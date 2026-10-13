package com.microsoft.openai.samples.assistant.external;

import com.microsoft.openai.samples.assistant.external.model.Invoice;
import com.microsoft.openai.samples.assistant.external.model.Payment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Thin gateway that forwards external MCP calls to the payment service's REST API,
 * so data lands in the same stores the workspace agent reads from.
 */
@Service
public class ExternalIntegrationService {

    private static final Logger logger = LoggerFactory.getLogger(ExternalIntegrationService.class);

    private final WebClient webClient;
    private final String paymentApiUrl;

    public ExternalIntegrationService(WebClient.Builder webClientBuilder, @Value("${payment.api.url}") String paymentApiUrl) {
        this.webClient = webClientBuilder.build();
        this.paymentApiUrl = paymentApiUrl;
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
}
