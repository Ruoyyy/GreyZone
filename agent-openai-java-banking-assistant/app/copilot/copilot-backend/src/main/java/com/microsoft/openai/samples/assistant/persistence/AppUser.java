package com.microsoft.openai.samples.assistant.persistence;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/** Identity info stored in the backend for the selectable users. */
@Entity
@Table(name = "app_user")
public class AppUser {

    @Id
    private String username;
    private String email;
    private String displayName;
    private String role;

    protected AppUser() {
    }

    public AppUser(String username, String email, String displayName, String role) {
        this.username = username;
        this.email = email;
        this.displayName = displayName;
        this.role = role;
    }

    public String getUsername() { return username; }
    public String getEmail() { return email; }
    public String getDisplayName() { return displayName; }
    public String getRole() { return role; }
}
