package com.microsoft.openai.samples.assistant.business.controller;

import com.microsoft.openai.samples.assistant.business.models.Invoice;
import com.microsoft.openai.samples.assistant.business.service.InvoiceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class InvoiceController {

    private static final Logger logger = LoggerFactory.getLogger(InvoiceController.class);

    private final InvoiceService invoiceService;

    public InvoiceController(InvoiceService invoiceService) {
        this.invoiceService = invoiceService;
    }

    @PostMapping("/invoices")
    public void submitInvoice(@RequestBody Invoice invoice) {
        logger.info("Received invoice: {}", invoice);
        invoiceService.save(invoice);
    }

    @GetMapping("/invoices/{invoiceId}")
    public Invoice getInvoice(@PathVariable String invoiceId) {
        logger.info("Received request to get invoice [{}]", invoiceId);
        return invoiceService.get(invoiceId);
    }
}
