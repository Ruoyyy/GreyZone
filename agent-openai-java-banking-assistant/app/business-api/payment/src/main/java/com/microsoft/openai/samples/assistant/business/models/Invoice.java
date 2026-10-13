package com.microsoft.openai.samples.assistant.business.models;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Structured invoice/bill data. The seven named fields mirror what the Document Intelligence
 * prebuilt-invoice flow extracts. Any extra fields submitted are preserved via additionalProperties,
 * so the schema can be extended without changing this class.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class Invoice {

    @JsonProperty("VendorName")
    private String vendorName;
    @JsonProperty("VendorAddress")
    private String vendorAddress;
    @JsonProperty("CustomerName")
    private String customerName;
    @JsonProperty("CustomerAddressRecipient")
    private String customerAddressRecipient;
    @JsonProperty("InvoiceId")
    private String invoiceId;
    @JsonProperty("InvoiceDate")
    private String invoiceDate;
    @JsonProperty("InvoiceTotal")
    private String invoiceTotal;

    private final Map<String, Object> additionalProperties = new LinkedHashMap<>();

    public String getVendorName() { return vendorName; }
    public void setVendorName(String vendorName) { this.vendorName = vendorName; }

    public String getVendorAddress() { return vendorAddress; }
    public void setVendorAddress(String vendorAddress) { this.vendorAddress = vendorAddress; }

    public String getCustomerName() { return customerName; }
    public void setCustomerName(String customerName) { this.customerName = customerName; }

    public String getCustomerAddressRecipient() { return customerAddressRecipient; }
    public void setCustomerAddressRecipient(String customerAddressRecipient) { this.customerAddressRecipient = customerAddressRecipient; }

    public String getInvoiceId() { return invoiceId; }
    public void setInvoiceId(String invoiceId) { this.invoiceId = invoiceId; }

    public String getInvoiceDate() { return invoiceDate; }
    public void setInvoiceDate(String invoiceDate) { this.invoiceDate = invoiceDate; }

    public String getInvoiceTotal() { return invoiceTotal; }
    public void setInvoiceTotal(String invoiceTotal) { this.invoiceTotal = invoiceTotal; }

    @JsonAnyGetter
    public Map<String, Object> getAdditionalProperties() { return additionalProperties; }

    @JsonAnySetter
    public void setAdditionalProperty(String name, Object value) { additionalProperties.put(name, value); }

    @Override
    public String toString() {
        return "Invoice{InvoiceId=" + invoiceId + ", VendorName=" + vendorName + ", InvoiceTotal=" + invoiceTotal
                + ", extra=" + additionalProperties + "}";
    }
}
