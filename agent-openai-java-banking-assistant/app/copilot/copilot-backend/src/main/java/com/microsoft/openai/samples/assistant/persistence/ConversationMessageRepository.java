package com.microsoft.openai.samples.assistant.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ConversationMessageRepository extends JpaRepository<ConversationMessage, Long> {
    List<ConversationMessage> findByUserIdOrderByIdAsc(String userId);
}
