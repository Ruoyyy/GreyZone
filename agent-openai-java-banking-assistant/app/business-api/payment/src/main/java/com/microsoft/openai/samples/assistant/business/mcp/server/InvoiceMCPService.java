package com.microsoft.openai.samples.assistant.business.mcp.server;

import com.microsoft.openai.samples.assistant.business.models.Invoice;
import com.microsoft.openai.samples.assistant.business.service.InvoiceService;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

@Service
public class InvoiceMCPService {

    private final InvoiceService invoiceService;

    public InvoiceMCPService(InvoiceService invoiceService) {
        this.invoiceService = invoiceService;
    }

    @Tool(description = "Get the extracted data of an invoice or bill by its invoice id")
    public Invoice getInvoice(@ToolParam(description = "The invoice id") String invoiceId) {
        return invoiceService.get(invoiceId);
    }
}
