package com.microsoft.openai.samples.assistant.persistence;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

/** Seeds the selectable identities on startup (idempotent). */
@Component
public class DataSeeder implements CommandLineRunner {

    private final AppUserRepository users;

    public DataSeeder(AppUserRepository users) {
        this.users = users;
    }

    @Override
    public void run(String... args) {
        seed("admin", "admin@contoso.com", "Admin", "admin");
        seed("ruoyu", "ruoyu@contoso.com", "Ruoyu", "user");
    }

    private void seed(String username, String email, String displayName, String role) {
        if (!users.existsById(username)) {
            users.save(new AppUser(username, email, displayName, role));
        }
    }
}
