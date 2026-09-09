"""
Interactive Terminal AI Assistant & Second Brain Client
======================================================
Connects to server.py via stdio MCP transport, performs memory hydration,
and runs an interactive REPL powered by Google Gemini API or OpenAI with dynamic tool calling.
"""

import asyncio
import json
import os
import sys
from typing import Dict, Any, List
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load environment variables from .env if present
load_dotenv()


def convert_mcp_tool_to_openai(mcp_tool: Any) -> Dict[str, Any]:
    """Converts an MCP tool definition to OpenAI's tool format."""
    schema = mcp_tool.inputSchema if hasattr(mcp_tool, "inputSchema") else {}
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            "parameters": schema
        }
    }


def clean_schema_for_gemini(d: Any) -> Any:
    """Recursively removes unsupported JSON schema fields like additionalProperties for Gemini API."""
    if isinstance(d, dict):
        return {
            k: clean_schema_for_gemini(v)
            for k, v in d.items()
            if k not in ("additionalProperties", "additional_properties", "$schema")
        }
    elif isinstance(d, list):
        return [clean_schema_for_gemini(i) for i in d]
    return d


def convert_mcp_tool_to_gemini(mcp_tool: Any, types_module: Any) -> Any:
    """Converts an MCP tool definition to Google GenAI tool format."""
    schema = mcp_tool.inputSchema if hasattr(mcp_tool, "inputSchema") else {}
    cleaned_schema = clean_schema_for_gemini(schema) if schema else None
    return types_module.FunctionDeclaration(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        parameters=cleaned_schema
    )


async def run_gemini_repl(session: ClientSession, available_tools: List[Any], system_prompt: str, gemini_api_key: str):
    from google import genai
    from google.genai import types

    gemini_client = genai.Client(api_key=gemini_api_key)
    func_declarations = [convert_mcp_tool_to_gemini(t, types) for t in available_tools]
    tools = [types.Tool(function_declarations=func_declarations)]

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=tools
    )

    chat = gemini_client.chats.create(model="gemini-3.6-flash", config=config)

    async def send_with_retry(msg_content: Any, max_retries: int = 3):
        import re
        for attempt in range(max_retries):
            try:
                return chat.send_message(msg_content)
            except Exception as exc:
                err_str = str(exc)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                    match = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str, re.IGNORECASE)
                    if not match:
                        match = re.search(r"retryDelay': '(\d+)s", err_str, re.IGNORECASE)
                    
                    wait_sec = float(match.group(1)) + 1.0 if match else 15.0
                    if wait_sec <= 65.0:
                        print(f"\n[!] Rate limit reached. Auto-waiting {int(wait_sec)}s for quota reset (attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(wait_sec)
                        continue
                raise exc

    while True:
        try:
            user_input = input("You > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                print("\nExiting Assistant session. Goodbye!")
                break

            response = await send_with_retry(user_input)

            while response.function_calls:
                for call in response.function_calls:
                    tool_name = call.name
                    args = dict(call.args) if call.args else {}

                    print(f"   [Tool Executing] -> {tool_name}({args})")
                    try:
                        tool_result = await session.call_tool(tool_name, args)
                        result_text = (
                            tool_result.content[0].text
                            if tool_result.content
                            else "Tool completed with no output."
                        )
                    except Exception as exec_err:
                        result_text = f"Tool Execution Error: {str(exec_err)}"

                    print(f"   [Tool Output] -> {result_text[:120]}..." if len(result_text) > 120 else f"   [Tool Output] -> {result_text}")

                    function_response_part = types.Part.from_function_response(
                        name=tool_name,
                        response={"result": result_text}
                    )
                    response = await send_with_retry(function_response_part)

            if response.text:
                print(f"\nBrain > {response.text}\n")

        except KeyboardInterrupt:
            print("\nSession interrupted. Type 'exit' to quit.")
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                print("\n[!] ERROR: Gemini API Quota / Rate Limit Exceeded (429 RESOURCE_EXHAUSTED).")
                print("    - Free tier limit reached.")
                print("    - Fix 1: Wait a minute for quota reset, then try your request again.")
                print("    - Fix 2: Get a fresh key from https://aistudio.google.com and update GEMINI_API_KEY in .env.")
                print("    - Fix 3: Or set OPENAI_API_KEY in your .env file.\n")
            else:
                print(f"\n[!] Error during execution loop: {err_str}\n")


async def run_openai_repl(session: ClientSession, available_tools: List[Any], system_prompt: str, openai_api_key: str):
    from openai import OpenAI

    openai_client = OpenAI(api_key=openai_api_key)
    openai_tools = [convert_mcp_tool_to_openai(t) for t in available_tools]
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    while True:
        try:
            user_input = input("You > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                print("\nExiting Assistant session. Goodbye!")
                break

            messages.append({"role": "user", "content": user_input})

            while True:
                try:
                    response = openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=messages,
                        tools=openai_tools,
                        tool_choice="auto"
                    )
                except Exception as api_err:
                    err_str = str(api_err)
                    if "insufficient_quota" in err_str or "429" in err_str:
                        print("\n[!] ERROR: OpenAI API Quota Exceeded (429 insufficient_quota).")
                        print("    - Your OpenAI account currently has $0 credit balance.")
                        print("    - Fix 1: Add credits at https://platform.openai.com/account/billing")
                        print("    - Fix 2: Or get a FREE key from https://aistudio.google.com and set GEMINI_API_KEY in .env\n")
                        return
                    else:
                        raise api_err

                choice = response.choices[0]
                response_message = choice.message

                msg_dict = {"role": "assistant", "content": response_message.content}
                if response_message.tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in response_message.tool_calls
                    ]

                messages.append(msg_dict)

                if response_message.tool_calls:
                    for tool_call in response_message.tool_calls:
                        tool_name = tool_call.function.name
                        raw_args = tool_call.function.arguments
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            args = {}

                        print(f"   [Tool Executing] -> {tool_name}({args})")

                        try:
                            tool_result = await session.call_tool(tool_name, args)
                            result_text = (
                                tool_result.content[0].text
                                if tool_result.content
                                else "Tool completed with no output."
                            )
                        except Exception as exec_err:
                            result_text = f"Tool Execution Error: {str(exec_err)}"

                        print(f"   [Tool Output] -> {result_text[:120]}..." if len(result_text) > 120 else f"   [Tool Output] -> {result_text}")

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_text
                        })
                    continue
                else:
                    if response_message.content:
                        print(f"\nBrain > {response_message.content}\n")
                    break

        except KeyboardInterrupt:
            print("\nSession interrupted. Type 'exit' to quit.")
        except Exception as e:
            print(f"\n[!] Error during execution loop: {str(e)}\n")


async def run_assistant():
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not gemini_key and not openai_key:
        print("[!] ERROR: No API key found.")
        print("Please set GEMINI_API_KEY or OPENAI_API_KEY in your environment or in a .env file.")
        sys.exit(1)

    server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script],
        env=os.environ.copy()
    )

    print("===============================================================")
    print("  Local OS Assistant & Second Brain (MCP Client)")
    print("===============================================================")
    print("[*] Launching Local OS & Brain MCP Server...")

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                print("[*] Initializing MCP Session...")
                await session.initialize()

                mcp_tools_res = await session.list_tools()
                available_tools = mcp_tools_res.tools

                print(f"[+] Connected successfully! {len(available_tools)} tools registered:")
                for tool in available_tools:
                    print(f"    - {tool.name}")

                print("\n[*] Hydrating Memory Context from 'brain.db'...")
                initial_memories = "No stored memories found."
                try:
                    memory_result = await session.call_tool("search_memory", {"query": ""})
                    if memory_result.content:
                        initial_memories = memory_result.content[0].text
                except Exception as mem_err:
                    print(f"[!] Warning: Memory hydration failed: {mem_err}")

                system_prompt = (
                    "You are an autonomous Senior AI Local OS Assistant & Second Brain.\n"
                    "You run locally on the user's computer and execute local system actions safely using MCP tools.\n"
                    "You remember cross-session context stored in SQLite memory.\n\n"
                    "RULES:\n"
                    "1. Always prefer using your local system tools to perform actions when asked.\n"
                    "2. To write or edit code/files on disk, use `write_local_file` with the full file content and path.\n"
                    "3. To execute code or run scripts, use `execute_terminal_command`.\n"
                    "4. Automatically check memory when relevant or save important context/preferences.\n"
                    "5. For terminal execution, double-check dangerous operations before executing.\n\n"
                    f"CURRENT STORED MEMORY HYDRATION:\n{initial_memories}"
                )

                if gemini_key:
                    print("\n[*] Active Model: Google Gemini API (gemini-3.6-flash)")
                    print("Assistant ready! Type your request or 'exit'/'quit' to stop.\n")
                    await run_gemini_repl(session, available_tools, system_prompt, gemini_key)
                elif openai_key:
                    print("\n[*] Active Model: OpenAI (gpt-4o)")
                    print("Assistant ready! Type your request or 'exit'/'quit' to stop.\n")
                    await run_openai_repl(session, available_tools, system_prompt, openai_key)

    except Exception as e:
        print(f"[!] Server communication failure: {str(e)}")


def main():
    asyncio.run(run_assistant())


if __name__ == "__main__":
    main()
