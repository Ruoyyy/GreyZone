import { useEffect, useState } from "react";
import { Spinner, Panel, MessageBar, MessageBarType, DefaultButton } from "@fluentui/react";
import DOMPurify from "dompurify";

import styles from "./Account.module.css";
import {
    AccountInfo,
    PaymentMethod,
    Beneficiary,
    Transaction,
    getAccountDetailsApi,
    getPaymentMethodApi,
    getBeneficiariesApi,
    getTransactionsApi,
    submitInvoiceApi,
    askAgentApi
} from "../../api";

// Known mock accounts seeded in the account service.
const ACCOUNTS = ["1000", "1010"];
// Default logged user account (matches backend LoggedUserService default: admin@contoso.com).
const DEFAULT_ACCOUNT = "1010";
// The seven invoice fields the backend expects (extra fields are still accepted by the API).
const INVOICE_FIELDS = ["InvoiceId", "VendorName", "InvoiceTotal", "InvoiceDate", "VendorAddress", "CustomerName", "CustomerAddressRecipient"];

const formatAmount = (amount: string, currency: string, type: string) => {
    const sign = type?.toLowerCase() === "income" ? "+" : "-";
    return `${sign}${currency} ${amount}`;
};

const formatTimestamp = (ts: string) => {
    // Backend returns canonical UTC second-precision ISO (e.g. 2024-04-01T12:00:00Z).
    // Render it uniformly as "YYYY-MM-DD HH:MM:SS" without locale drift.
    const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})Z?$/.exec(ts);
    return m ? `${m[1]} ${m[2]}` : ts;
};

const Account = () => {
    const [accountId, setAccountId] = useState<string>(DEFAULT_ACCOUNT);
    const [account, setAccount] = useState<AccountInfo | null>(null);
    const [methods, setMethods] = useState<PaymentMethod[]>([]);
    const [beneficiaries, setBeneficiaries] = useState<Beneficiary[]>([]);
    const [transactions, setTransactions] = useState<Transaction[]>([]);
    const [loading, setLoading] = useState<boolean>(false);
    const [error, setError] = useState<string>("");
    const [receipt, setReceipt] = useState<Transaction | null>(null);

    const [invoice, setInvoice] = useState<Record<string, string>>({});
    const [invoiceStatus, setInvoiceStatus] = useState<string>("");
    const [agentPrompt, setAgentPrompt] = useState<string>("");
    const [agentResponse, setAgentResponse] = useState<string>("");
    const [agentBusy, setAgentBusy] = useState<boolean>(false);

    const submitInvoice = async () => {
        setInvoiceStatus("");
        const payload = Object.fromEntries(Object.entries(invoice).filter(([, v]) => v && v.trim() !== ""));
        if (!payload.InvoiceId) {
            setInvoiceStatus("InvoiceId is required.");
            return;
        }
        try {
            await submitInvoiceApi(payload);
            setInvoiceStatus(`Invoice ${payload.InvoiceId} submitted.`);
            setAgentPrompt(`I want to pay the bill with invoice id ${payload.InvoiceId}. Please look it up.`);
        } catch (e: any) {
            setInvoiceStatus(e?.message ?? "Submit failed");
        }
    };

    const runAgent = async () => {
        if (!agentPrompt.trim()) return;
        setAgentBusy(true);
        setAgentResponse("");
        try {
            setAgentResponse(await askAgentApi(agentPrompt));
        } catch (e: any) {
            setAgentResponse("Error: " + (e?.message ?? "agent failed"));
        } finally {
            setAgentBusy(false);
        }
    };

    const load = async (id: string) => {
        setLoading(true);
        setError("");
        try {
            const [acc, bens, txs] = await Promise.all([getAccountDetailsApi(id), getBeneficiariesApi(id), getTransactionsApi(id)]);
            setAccount(acc);
            setBeneficiaries(bens);
            setTransactions(txs);

            // Fetch full payment-method details (available balance, card number) for each summary.
            const summaries = acc?.paymentMethods ?? [];
            const details = await Promise.all(summaries.map(s => getPaymentMethodApi(id, s.id).catch(() => null)));
            setMethods(details.filter((m): m is PaymentMethod => m !== null));
        } catch (e: any) {
            setError(e?.message ?? "Failed to load account data");
            setAccount(null);
            setMethods([]);
            setBeneficiaries([]);
            setTransactions([]);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load(accountId);
    }, [accountId]);

    return (
        <div className={styles.container}>
            <div className={styles.toolbar}>
                <h2 className={styles.pageTitle}>Account Details</h2>
                <div className={styles.toolbarRight}>
                    <label htmlFor="account-select" className={styles.selectLabel}>
                        Account
                    </label>
                    <select id="account-select" className={styles.select} value={accountId} onChange={e => setAccountId(e.target.value)}>
                        {ACCOUNTS.map(a => (
                            <option key={a} value={a}>
                                {a}
                            </option>
                        ))}
                    </select>
                    <DefaultButton text="Refresh" iconProps={{ iconName: "Refresh" }} onClick={() => load(accountId)} disabled={loading} />
                </div>
            </div>

            {loading && <Spinner label="Loading account data..." className={styles.spinner} />}
            {error && (
                <MessageBar messageBarType={MessageBarType.error} className={styles.messageBar}>
                    {error}
                </MessageBar>
            )}

            {!loading && !error && account && (
                <>
                    <div className={styles.cards}>
                        <div className={styles.balanceCard}>
                            <div className={styles.balanceLabel}>Balance</div>
                            <div className={styles.balanceValue}>
                                {account.currency} {account.balance}
                            </div>
                            <div className={styles.balanceSub}>
                                {account.accountHolderFullName} · #{account.id}
                            </div>
                            <div className={styles.balanceSub}>
                                {account.userName} · active since {account.activationDate}
                            </div>
                        </div>

                        <div className={styles.card}>
                            <div className={styles.cardTitle}>Payment Methods</div>
                            {methods.length === 0 && <div className={styles.empty}>No payment methods.</div>}
                            {methods.map(m => (
                                <div key={m.id} className={styles.methodRow}>
                                    <div>
                                        <strong>{m.type}</strong>
                                        {m.cardNumber && <span className={styles.muted}> · ****{m.cardNumber.slice(-4)}</span>}
                                        <div className={styles.muted}>expires {m.expirationDate}</div>
                                    </div>
                                    <div className={styles.methodBalance}>
                                        {account.currency} {m.availableBalance}
                                    </div>
                                </div>
                            ))}
                        </div>

                        <div className={styles.card}>
                            <div className={styles.cardTitle}>Registered Beneficiaries</div>
                            {beneficiaries.length === 0 && <div className={styles.empty}>No beneficiaries.</div>}
                            {beneficiaries.map(b => (
                                <div key={b.id} className={styles.methodRow}>
                                    <div>
                                        <strong>{b.fullName}</strong>
                                        <div className={styles.muted}>{b.bankName}</div>
                                    </div>
                                    <div className={styles.muted}>{b.bankCode}</div>
                                </div>
                            ))}
                        </div>
                    </div>

                    <div className={styles.card}>
                        <div className={styles.cardTitle}>Transaction History</div>
                        {transactions.length === 0 && <div className={styles.empty}>No transactions.</div>}
                        {transactions.length > 0 && (
                            <table className={styles.table}>
                                <thead>
                                    <tr>
                                        <th>Date / Time</th>
                                        <th>Description</th>
                                        <th>Counterparty</th>
                                        <th>Method</th>
                                        <th className={styles.right}>Amount</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {transactions.map(t => (
                                        <tr key={t.id}>
                                            <td>{formatTimestamp(t.timestamp)}</td>
                                            <td>{t.description}</td>
                                            <td>{t.recipientName}</td>
                                            <td>{t.paymentType}</td>
                                            <td className={`${styles.right} ${t.type?.toLowerCase() === "income" ? styles.income : styles.outcome}`}>
                                                {formatAmount(t.amount, account.currency, t.type)}
                                            </td>
                                            <td className={styles.right}>
                                                <button className={styles.linkButton} onClick={() => setReceipt(t)}>
                                                    Receipt
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>

                    <div className={styles.card}>
                        <div className={styles.cardTitle}>Invoice (bill) — submit &amp; agent test</div>
                        <div className={styles.invoiceGrid}>
                            {INVOICE_FIELDS.map(f => (
                                <label key={f} className={styles.field}>
                                    <span className={styles.fieldLabel}>
                                        {f}
                                        {f === "InvoiceId" ? " *" : ""}
                                    </span>
                                    <input
                                        className={styles.input}
                                        value={invoice[f] ?? ""}
                                        onChange={e => setInvoice({ ...invoice, [f]: e.target.value })}
                                        placeholder={f === "InvoiceTotal" ? "€85.20" : ""}
                                    />
                                </label>
                            ))}
                        </div>
                        <div className={styles.actions}>
                            <DefaultButton text="Submit invoice" onClick={submitInvoice} />
                            {invoiceStatus && <span className={styles.statusMsg}>{invoiceStatus}</span>}
                        </div>

                        <div className={styles.agentTest}>
                            <div className={styles.cardSubtitle}>Ask the agent</div>
                            <textarea
                                className={styles.textarea}
                                rows={2}
                                value={agentPrompt}
                                onChange={e => setAgentPrompt(e.target.value)}
                                placeholder="e.g. I want to pay the bill with invoice id 9524011000817857"
                            />
                            <div className={styles.actions}>
                                <DefaultButton text={agentBusy ? "Asking..." : "Send to agent"} onClick={runAgent} disabled={agentBusy} />
                            </div>
                            {agentResponse && (
                                <div className={styles.agentResponse} dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(agentResponse) }} />
                            )}
                        </div>
                    </div>
                </>
            )}

            <Panel isOpen={!!receipt} onDismiss={() => setReceipt(null)} headerText="Transaction Receipt" closeButtonAriaLabel="Close">
                {receipt && account && (
                    <div className={styles.receipt}>
                        <div className={styles.receiptAmount}>{formatAmount(receipt.amount, account.currency, receipt.type)}</div>
                        <dl className={styles.receiptList}>
                            <dt>Type</dt>
                            <dd>{receipt.type}</dd>
                            <dt>Date / Time</dt>
                            <dd>{formatTimestamp(receipt.timestamp)}</dd>
                            <dt>Counterparty</dt>
                            <dd>{receipt.recipientName}</dd>
                            <dt>Payment Type</dt>
                            <dd>{receipt.paymentType}</dd>
                            <dt>Description</dt>
                            <dd>{receipt.description}</dd>
                            <dt>Account</dt>
                            <dd>#{receipt.accountId}</dd>
                            {receipt.recipientBankReference && (
                                <>
                                    <dt>Bank Reference</dt>
                                    <dd>{receipt.recipientBankReference}</dd>
                                </>
                            )}
                            <dt>Transaction ID</dt>
                            <dd className={styles.muted}>{receipt.id}</dd>
                        </dl>
                    </div>
                )}
            </Panel>
        </div>
    );
};

export default Account;
