package com.microsoft.openai.samples.assistant.business.service;

import com.microsoft.openai.samples.assistant.business.models.Account;
import org.springframework.stereotype.Service;

import java.util.*;


@Service
public class UserService {


    private Map<String, List<Account>> accounts = new HashMap<>();

    public UserService() {
        accounts.put(
                "admin",
                List.of(new Account(
                        "1010",
                        "admin@contoso.com",
                        "Admin User",
                        "EUR",
                        "2022-01-01",
                        "10000",
                        null
                ))
        );
        accounts.put(
                "ruoyu",
                List.of(new Account(
                        "1000",
                        "ruoyu@contoso.com",
                        "Ruoyu User",
                        "USD",
                        "2022-01-01",
                        "5000",
                        null
                ))
        );

     }
    public List<Account> getAccountsByUserName(String userName) {
        return accounts.getOrDefault(userName, List.of());
    }


}
