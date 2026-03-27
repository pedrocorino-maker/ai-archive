"""AI Archive — pytest fixtures."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_archive.db import init_db
from ai_archive.models import (
    CodeBlock,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    Provider,
)


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """Return an initialized, empty SQLite connection."""
    db_path = tmp_path / "test_archive.db"
    conn = init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def sample_message_user() -> Message:
    """A realistic user message."""
    return Message(
        provider_message_id="msg_user_001",
        role=MessageRole.USER,
        author="user",
        timestamp=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        raw_text="How do I reverse a string in Python?",
        normalized_text="How do I reverse a string in Python?",
        ordinal=0,
    )


@pytest.fixture
def sample_message_assistant() -> Message:
    """A realistic assistant message with a code block."""
    code = 'text = "hello"\nreversed_text = text[::-1]\nprint(reversed_text)  # "olleh"'
    return Message(
        provider_message_id="msg_asst_001",
        role=MessageRole.ASSISTANT,
        author="assistant",
        timestamp=datetime(2024, 6, 15, 10, 0, 5, tzinfo=timezone.utc),
        raw_text=(
            "You can reverse a string in Python using slicing:\n\n"
            f"```python\n{code}\n```\n\n"
            "This uses Python's slice notation with a step of -1 to reverse the string."
        ),
        normalized_text=(
            "You can reverse a string in Python using slicing:\n\n"
            f"```python\n{code}\n```\n\n"
            "This uses Python's slice notation with a step of -1 to reverse the string."
        ),
        code_blocks=[CodeBlock(language="python", code=code, ordinal=0)],
        ordinal=1,
    )


@pytest.fixture
def sample_conversation(
    sample_message_user: Message,
    sample_message_assistant: Message,
) -> Conversation:
    """A realistic fake Conversation with 3 messages."""
    follow_up = Message(
        provider_message_id="msg_user_002",
        role=MessageRole.USER,
        author="user",
        timestamp=datetime(2024, 6, 15, 10, 1, 0, tzinfo=timezone.utc),
        raw_text="What about reversing a list?",
        normalized_text="What about reversing a list?",
        ordinal=2,
    )
    messages = [sample_message_user, sample_message_assistant, follow_up]
    conv = Conversation(
        id="testconv0000001",
        provider=Provider.CHATGPT,
        provider_conversation_id="abc123testconv",
        title="Python String Reversal",
        url="https://chatgpt.com/c/abc123testconv",
        created_at=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2024, 6, 15, 10, 1, 0, tzinfo=timezone.utc),
        extracted_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        model_name="gpt-4o",
        message_count=len(messages),
        messages=messages,
        status=ConversationStatus.ACTIVE,
        canonical_text="[user] How do I reverse a string in Python?\n\n[assistant] You can reverse...",
    )
    conv.content_hash = conv.compute_hash()
    return conv


@pytest.fixture
def sample_html_chatgpt() -> str:
    """Minimal but realistic ChatGPT conversation page HTML fixture."""
    return """<!DOCTYPE html>
<html lang="en">
<head><title>Python String Reversal - ChatGPT</title></head>
<body>
  <nav aria-label="Chat history">
    <ol>
      <li><a href="/c/abc123testconv">Python String Reversal</a></li>
      <li><a href="/c/def456another">Another Chat</a></li>
    </ol>
  </nav>
  <main>
    <h1 data-testid="conversation-title">Python String Reversal</h1>
    <div data-testid="user-menu-button" aria-label="User account menu">
      <img alt="User avatar" src="/avatar.png" />
    </div>
    <div data-message-author-role="user" data-testid="conversation-turn">
      <div class="whitespace-pre-wrap">How do I reverse a string in Python?</div>
    </div>
    <div data-message-author-role="assistant" data-testid="conversation-turn">
      <div class="markdown">
        <p>You can reverse a string in Python using slicing:</p>
        <pre><code class="language-python">text = "hello"
reversed_text = text[::-1]
print(reversed_text)  # "olleh"
</code></pre>
        <p>This uses Python's slice notation with a step of -1 to reverse the string.</p>
      </div>
    </div>
    <div data-message-author-role="user" data-testid="conversation-turn">
      <div class="whitespace-pre-wrap">What about reversing a list?</div>
    </div>
    <div data-message-author-role="assistant" data-testid="conversation-turn">
      <div class="markdown">
        <p>To reverse a list in Python, you can use:</p>
        <pre><code class="language-python">my_list = [1, 2, 3, 4, 5]
reversed_list = my_list[::-1]
# or in-place:
my_list.reverse()
</code></pre>
      </div>
    </div>
  </main>
  <button data-testid="model-switcher-dropdown-button">GPT-4o</button>
</body>
</html>"""


@pytest.fixture
def sample_html_gemini() -> str:
    """Minimal but realistic Gemini conversation page HTML fixture."""
    return """<!DOCTYPE html>
<html lang="en">
<head><title>Python Tips - Gemini</title></head>
<body>
  <div class="conversation-list">
    <bard-sidenav-item data-conversation-id="gemini001" aria-selected="true">
      <span class="conversation-title">Python Tips</span>
    </bard-sidenav-item>
    <bard-sidenav-item data-conversation-id="gemini002">
      <span class="conversation-title">Machine Learning Basics</span>
    </bard-sidenav-item>
  </div>
  <main>
    <h1 class="conversation-title">Python Tips</h1>
    <a aria-label="Google Account: test@example.com" class="gb_A" href="#">
      <img alt="profile picture" src="/profile.jpg" />
    </a>
    <div class="conversation-content">
      <user-query>
        <div class="query-text" data-role="user">
          <p>What is a list comprehension in Python?</p>
        </div>
      </user-query>
      <model-response data-role="model">
        <div class="response-content">
          <p>A list comprehension is a concise way to create lists in Python.</p>
          <div class="code-block">
            <pre><code class="language-python">squares = [x**2 for x in range(10)]
print(squares)  # [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]
</code></pre>
          </div>
          <p>It's generally faster and more readable than using a for loop.</p>
        </div>
      </model-response>
      <user-query>
        <div class="query-text" data-role="user">
          <p>Can you also show a conditional list comprehension?</p>
        </div>
      </user-query>
      <model-response data-role="model">
        <div class="response-content">
          <p>Yes! Here's a conditional list comprehension:</p>
          <div class="code-block">
            <pre><code class="language-python">evens = [x for x in range(20) if x % 2 == 0]
print(evens)  # [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
</code></pre>
          </div>
        </div>
      </model-response>
    </div>
  </main>
</body>
</html>"""
