import { ChatAppResponse, ChatAppResponseOrError, ChatAppRequest, AccountInfo, PaymentMethod, Beneficiary, Transaction } from "./models";
import { useLogin } from "../authConfig";

const BACKEND_URI = import.meta.env.VITE_BACKEND_URI ? import.meta.env.VITE_BACKEND_URI : "";

// Business services are proxied by nginx (see nginx.conf.template) so the browser can read them directly.
const ACCOUNT_API = "/account-api";
const TRANSACTION_API = "/transaction-api";
const PAYMENT_API = "/payment-api";

function getHeaders(idToken: string | undefined, stream:boolean): Record<string, string> {
    var headers: Record<string, string> = {
        "Content-Type": "application/json"
    };
    // If using login, add the id token of the logged in account as the authorization
    if (useLogin) {
        if (idToken) {
            headers["Authorization"] = `Bearer ${idToken}`
        }
    }

    if (stream) {
        headers["Accept"] = "application/x-ndjson";
    } else {
        headers["Accept"] = "application/json";
    }

    return headers;
}

export async function askApi(request: ChatAppRequest, idToken: string | undefined): Promise<ChatAppResponse> {
    const response = await fetch(`${BACKEND_URI}/ask`, {
        method: "POST",
        headers: getHeaders(idToken, request.stream || false),
        body: JSON.stringify(request)
    });

    const parsedResponse: ChatAppResponseOrError = await response.json();
    if (response.status > 299 || !response.ok) {
        throw Error(parsedResponse.error || "Unknown error");
    }

    return parsedResponse as ChatAppResponse;
}

export async function chatApi(request: ChatAppRequest, idToken: string | undefined): Promise<Response> {
    return await fetch(`${BACKEND_URI}/chat`, {
        method: "POST",
        headers: getHeaders(idToken, request.stream || false),
        body: JSON.stringify(request)
    });
}

export function getCitationFilePath(citation: string): string {
    return `${BACKEND_URI}/content/${citation}`;
}

export function uploadAttachment(file: File): Promise<string> {
    const formData = new FormData();
    formData.append("file", file);

    return fetch(`${BACKEND_URI}/content`, {
        method: "POST",
        body: formData
    }).then(response => {
        if (response.status > 299 || !response.ok) {
            throw Error("Failed to upload attachment");
        }
        return response.text();
    });
}

export function getImage(name: string): string {
        return `${BACKEND_URI}/content/${name}`;
    }

export async function getUserAccountsApi(userName: string): Promise<AccountInfo[]> {
    const res = await fetch(`${ACCOUNT_API}/users/${encodeURIComponent(userName)}/accounts`);
    if (!res.ok) throw Error(`Failed to load accounts for ${userName} (HTTP ${res.status})`);
    return res.json();
}

export async function getAccountDetailsApi(accountId: string): Promise<AccountInfo> {
    const res = await fetch(`${ACCOUNT_API}/accounts/${encodeURIComponent(accountId)}`);
    if (!res.ok) throw Error(`Failed to load account ${accountId} (HTTP ${res.status})`);
    return res.json();
}

export async function getPaymentMethodApi(accountId: string, methodId: string): Promise<PaymentMethod> {
    const res = await fetch(`${ACCOUNT_API}/accounts/${encodeURIComponent(accountId)}/paymentmethods/${encodeURIComponent(methodId)}`);
    if (!res.ok) throw Error(`Failed to load payment method ${methodId} (HTTP ${res.status})`);
    return res.json();
}

export async function getBeneficiariesApi(accountId: string): Promise<Beneficiary[]> {
    const res = await fetch(`${ACCOUNT_API}/accounts/${encodeURIComponent(accountId)}/registeredBeneficiaries`);
    if (!res.ok) throw Error(`Failed to load beneficiaries for ${accountId} (HTTP ${res.status})`);
    return res.json();
}

export async function getTransactionsApi(accountId: string): Promise<Transaction[]> {
    const res = await fetch(`${TRANSACTION_API}/transactions/${encodeURIComponent(accountId)}`);
    if (!res.ok) throw Error(`Failed to load transactions for ${accountId} (HTTP ${res.status})`);
    return res.json();
}

export async function submitInvoiceApi(invoice: Record<string, string>): Promise<void> {
    const res = await fetch(`${PAYMENT_API}/invoices`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invoice)
    });
    if (!res.ok) throw Error(`Failed to submit invoice (HTTP ${res.status})`);
}

export async function getInvoiceApi(invoiceId: string): Promise<Record<string, string>> {
    const res = await fetch(`${PAYMENT_API}/invoices/${encodeURIComponent(invoiceId)}`);
    if (!res.ok) throw Error(`Failed to load invoice ${invoiceId} (HTTP ${res.status})`);
    return res.json();
}

export async function askAgentApi(question: string): Promise<string> {
    const res = await fetch(`${BACKEND_URI}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ messages: [{ role: "user", content: question }], stream: false })
    });
    const data = await res.json();
    if (!res.ok) throw Error(data.error || `HTTP ${res.status}`);
    return data.choices?.[0]?.message?.content ?? "(no content)";
}

