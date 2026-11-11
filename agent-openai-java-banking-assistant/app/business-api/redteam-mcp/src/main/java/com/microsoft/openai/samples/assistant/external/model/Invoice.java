package com.microsoft.openai.samples.assistant.external.model;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Mirrors the payment service Invoice: seven named fields plus preserved additional fields.
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
}
