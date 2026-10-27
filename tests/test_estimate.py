from app import estimate


def test_empty_call_is_zero():
    assert estimate.estimate_prompt([], None) == 0
    assert estimate.estimate_completion([]) == 0


def test_prompt_counts_messages_json():
    msgs = [{"role": "user", "content": "hello world, how are you today?"}]
    n = estimate.estimate_prompt(msgs, None)
    assert n > 0
    assert n == estimate.estimate_prompt(msgs, None)  # deterministic


def test_tools_add_tokens():
    msgs = [{"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]
    assert estimate.estimate_prompt(msgs, tools) > estimate.estimate_prompt(msgs, None)


def test_completion_uses_tool_args_when_content_empty():
    choices = [
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "f", "arguments": '{"city": "Berlin"}'}}
                ],
            }
        }
    ]
    assert estimate.estimate_completion(choices) > 0
