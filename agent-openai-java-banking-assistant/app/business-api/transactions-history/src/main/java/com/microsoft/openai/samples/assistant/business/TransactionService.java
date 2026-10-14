package com.microsoft.openai.samples.assistant.business;

import com.microsoft.openai.samples.assistant.business.Transaction;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class TransactionService {

    private Map<String,List<Transaction>> lastTransactions= new HashMap<>();
    private Map<String,List<Transaction>> allTransactions= new HashMap<>();

    // Canonical output format: UTC, second precision (e.g. 2024-04-01T12:00:00Z).
    private static final DateTimeFormatter CANONICAL =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneOffset.UTC);
    // Loose parser for hand-written literals with non-padded month/day (e.g. 2024-4-01T12:00:00Z).
    private static final DateTimeFormatter LOOSE =
            DateTimeFormatter.ofPattern("yyyy-M-d'T'HH:mm:ssXXX");

    private String normalizeTimestamp(String ts) {
        if (ts == null || ts.isBlank()) return ts;
        try {
            // Handles strict ISO-8601 including fractional seconds, e.g. 2026-10-13T03:15:22.352385376Z.
            return CANONICAL.format(Instant.parse(ts));
        } catch (Exception ignored) {
            try {
                return CANONICAL.format(OffsetDateTime.parse(ts, LOOSE).toInstant());
            } catch (Exception ignored2) {
                return ts;
            }
        }
    }

    private Transaction withNormalizedTimestamp(Transaction t) {
        return new Transaction(t.id(), t.description(), t.type(), t.recipientName(), t.recipientBankReference(),
                t.accountId(), t.paymentType(), t.amount(), normalizeTimestamp(t.timestamp()));
    }

     public TransactionService(){

         lastTransactions.put("1010",new ArrayList<> (Arrays.asList(
                new Transaction("11", "Payment of the bill 334398", "outcome","acme", "0001", "1010", "BankTransfer", "100.00", "2024-4-01T12:00:00Z"),
                new Transaction("22", "Payment of the bill 4613","outcome", "contoso", "0002", "1010", "CreditCard", "200.00", "2024-3-02T12:00:00Z"),
                new Transaction("33", "Payment of the bill 724563","outcome", "duff", "0003", "1010", "BankTransfer", "300.00", "2023-10-03T12:00:00Z"),
                new Transaction("43", "Payment of the bill 8898943","outcome", "wayne enterprises", "0004", "1010", "DirectDebit", "400.00", "2023-8-04T12:00:00Z"),
                new Transaction("53", "Payment of the bill 19dee","outcome", "oscorp", "0005", "1010", "BankTransfer", "500.00", "2023-4-05T12:00:00Z"))
         ));


         allTransactions.put("1010",new ArrayList<>(Arrays.asList(
                new Transaction("11", "payment of bill id with 0001","outcome", "acme", "A012TABTYT156!", "1010", "BankTransfer", "100.00", "2024-4-01T12:00:00Z"),
                new Transaction("21", "Payment of the bill 4200","outcome", "acme", "0002", "1010", "BankTransfer", "200.00", "2024-1-02T12:00:00Z"),
                new Transaction("31", "Payment of the bill 3743","outcome", "acme", "0003", "1010", "DirectDebit", "300.00", "2023-10-03T12:00:00Z"),
                new Transaction("41", "Payment of the bill 8921","outcome", "acme", "0004", "1010", "Transfer", "400.00", "2023-8-04T12:00:00Z"),
                new Transaction("51", "Payment of the bill 7666","outcome", "acme", "0005", "1010", "CreditCard", "500.00", "2023-4-05T12:00:00Z"),

                new Transaction("12", "Payment of the bill 5517","outcome", "contoso", "0001", "1010", "CreditCard", "100.00", "2024-3-01T12:00:00Z"),
                new Transaction("22", "Payment of the bill 682222","outcome", "contoso", "0002", "1010", "CreditCard", "200.00", "2023-1-02T12:00:00Z"),
                new Transaction("32", "Payment of the bill 94112","outcome", "contoso", "0003", "1010", "Transfer", "300.00", "2022-10-03T12:00:00Z"),
                new Transaction("42", "Payment of the bill 23122","outcome", "contoso", "0004", "1010", "Transfer", "400.00", "2022-8-04T12:00:00Z"),
                new Transaction("52", "Payment of the bill 171443","outcome", "contoso", "0005", "1010", "Transfer", "500.00", "2020-4-05T12:00:00Z")
         )));

         // Other accounts start with empty (but present) history so payments/incoming don't fail.
         lastTransactions.put("1000", new ArrayList<>());
         allTransactions.put("1000", new ArrayList<>());

     }
    public List<Transaction> getTransactionsByRecipientName(String accountId, String name) {

        if (accountId == null || accountId.isEmpty())
            throw new IllegalArgumentException("AccountId is empty or null");
        try {
            Integer.parseInt(accountId);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("AccountId is not a valid number");
        }

    if ( allTransactions.get(accountId) == null) return new ArrayList<>();
        else
      return  allTransactions.get(accountId).stream()
                .filter(transaction -> transaction.recipientName().toLowerCase().contains(name.toLowerCase()))
                .map(this::withNormalizedTimestamp)
                .collect(Collectors.toList());

    }

    public List<Transaction> getlastTransactions(String accountId) {
        if (accountId == null || accountId.isEmpty())
            throw new IllegalArgumentException("AccountId is empty or null");
        try {
            Integer.parseInt(accountId);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("AccountId is not a valid number");
        }

        if ( lastTransactions.get(accountId) == null) return new ArrayList<>();
        else
        return lastTransactions.get(accountId).stream()
                .map(this::withNormalizedTimestamp)
                .collect(Collectors.toList());
    }

    public void notifyTransaction(String accountId,Transaction transaction){
        if (accountId == null || accountId.isEmpty())
            throw new IllegalArgumentException("AccountId is empty or null");
        try {
            Integer.parseInt(accountId);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("AccountId is not a valid number");
        }

         var transactionsList = allTransactions.get(accountId);
         if ( transactionsList == null)
             throw new RuntimeException("Cannot find all transactions for account id: "+accountId);
        transactionsList.add(transaction);

        var lastTransactionsList = lastTransactions.get(accountId);
        if ( lastTransactionsList == null)
            throw new RuntimeException("Cannot find last transactions for account id: "+accountId);
        lastTransactionsList.add(transaction);


    }
}
