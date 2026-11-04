package issue

import "testing"

func TestLettaCommentBody(t *testing.T) {
	tests := []struct{ body, want string }{
		{"", ""},
		{" \n\t", " \n\t"},
		{"Continue", "@letta-code Continue"},
		{"continue\nnext step", "@letta-code continue\nnext step"},
		{"@letta-code Continue", "@letta-code Continue"},
		{"@letta-code\nContinue", "@letta-code\nContinue"},
		{"Please ask @letta-code", "@letta-code Please ask @letta-code"},
		{"  Continue", "@letta-code   Continue"},
	}
	for _, tt := range tests {
		if got := lettaCommentBody(tt.body); got != tt.want {
			t.Errorf("lettaCommentBody(%q) = %q, want %q", tt.body, got, tt.want)
		}
		if got := lettaCommentBody(lettaCommentBody(tt.body)); got != tt.want {
			t.Errorf("prefix is not idempotent for %q: %q", tt.body, got)
		}
	}
}
