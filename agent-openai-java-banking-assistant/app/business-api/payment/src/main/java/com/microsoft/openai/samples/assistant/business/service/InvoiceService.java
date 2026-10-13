package com.microsoft.openai.samples.assistant.business.service;

import com.microsoft.openai.samples.assistant.business.models.Invoice;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class InvoiceService {

    private static final Logger logger = LoggerFactory.getLogger(InvoiceService.class);

    private final Map<String, Invoice> invoices = new ConcurrentHashMap<>();

    public void save(Invoice invoice) {
        if (invoice == null || invoice.getInvoiceId() == null || invoice.getInvoiceId().isBlank()) {
            throw new IllegalArgumentException("InvoiceId is empty or null");
        }
        invoices.put(invoice.getInvoiceId(), invoice);
        logger.info("Stored invoice [{}]", invoice);
    }

    public Invoice get(String invoiceId) {
        if (invoiceId == null || invoiceId.isBlank()) {
            throw new IllegalArgumentException("InvoiceId is empty or null");
        }
        return invoices.get(invoiceId);
    }
}
