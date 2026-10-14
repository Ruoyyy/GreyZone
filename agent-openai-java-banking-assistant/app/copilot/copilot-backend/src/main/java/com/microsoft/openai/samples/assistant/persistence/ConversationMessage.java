package com.microsoft.openai.samples.assistant.persistence;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/** One persisted chat turn, isolated per user (one conversation line per user). */
@Entity
@Table(name = "chat_message")
public class ConversationMessage {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private String userId;

    @Column(nullable = false)
    private String role; // "user" | "assistant"

    @Column(columnDefinition = "text")
    private String content;

    @Column(columnDefinition = "text")
    private String attachments;

    private Instant createdAt;

    protected ConversationMessage() {
    }

    public ConversationMessage(String userId, String role, String content, String attachments) {
        this.userId = userId;
        this.role = role;
        this.content = content;
        this.attachments = attachments;
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getUserId() { return userId; }
    public String getRole() { return role; }
    public String getContent() { return content; }
    public String getAttachments() { return attachments; }
    public Instant getCreatedAt() { return createdAt; }
}
