"""
ULTRON Voice Assistant & Second Brain Client
=============================================
Full-duplex, hands-free Voice Assistant powered by:
- Model Context Protocol (MCP) tool transport with server.py
- Picovoice Porcupine wake-word detection ("Ultron") with Manual Enter fallback
- faster-whisper local offline Speech-to-Text (STT)
- OpenAI gpt-4o / Google Gemini AI model tool execution
- edge-tts (en-US-ChristopherNeural) + pygame async Text-to-Speech (TTS)
"""

import asyncio
import json
import os
import struct
import sys
import tempfile
import time
import wave
from typing import Dict, Any, List, Optional

import edge_tts
import numpy as np
from dotenv import load_dotenv

import pvporcupine
import pyaudio
import pygame
from faster_whisper import WhisperModel

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load environment variables
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


async def speak_text(text: str, voice: str = "en-US-ChristopherNeural"):
    """Synthesizes AI text output to speech using edge-tts and plays audio asynchronously with pygame."""
    if not text or not text.strip():
        return

    clean_text = (
        text.replace("*", "")
        .replace("`", "")
        .replace("#", "")
        .replace("[", "")
        .replace("]", "")
        .strip()
    )

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        communicate = edge_tts.Communicate(clean_text, voice=voice)
        await communicate.save(tmp_path)

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.05)

        pygame.mixer.music.unload()
    except Exception as e:
        print(f"[!] TTS Playback Error: {e}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def play_wake_cue():
    """Triggers an audio cue / response when wake word or voice prompt is activated."""
    print("[!] ULTRON: 'I am listening...'")
    await speak_text("I am listening.", voice="en-US-ChristopherNeural")


def init_porcupine(access_key: str):
    """Initializes Picovoice Porcupine wake word engine with 'ultron' or keyword fallback."""
    keyword_path = os.getenv("PICOVOICE_KEYWORD_PATH")
    if keyword_path and os.path.exists(keyword_path):
        print(f"[*] Loading custom Porcupine keyword file: {keyword_path}")
        return pvporcupine.create(access_key=access_key, keyword_paths=[keyword_path])

    try:
        return pvporcupine.create(access_key=access_key, keywords=["ultron"])
    except Exception as e:
        print(f"[!] Warning: Built-in keyword 'ultron' not directly supported by default set: {e}")
        fallback_keywords = ["porcupine", "jarvis", "computer", "alexa"]
        for kw in fallback_keywords:
            try:
                porcupine = pvporcupine.create(access_key=access_key, keywords=[kw])
                print(f"[*] Initialized Porcupine using fallback wake word: '{kw.upper()}'")
                return porcupine
            except Exception:
                continue
        raise RuntimeError("Failed to initialize Porcupine wake word engine with available keywords.")


def record_speech_until_silence(
    pa: pyaudio.PyAudio,
    sample_rate: int = 16000,
    silence_threshold: float = 300.0,
    silence_duration_sec: float = 1.8,
    max_record_sec: float = 15.0
) -> Optional[str]:
    """
    Records mic audio post-wake word until silence is detected or max duration is reached.
    Saves and returns the filepath of a temporary WAV file.
    """
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1

    try:
        stream = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=sample_rate,
            input=True,
            frames_per_buffer=CHUNK
        )
    except Exception as e:
        print(f"[!] Error opening mic stream for recording: {e}")
        return None

    print("[*] Recording spoken command (speak now)...")
    frames = []
    silent_chunks = 0
    speech_started = False
    start_time = time.time()
    chunks_per_sec = sample_rate / CHUNK
    max_silent_chunks = int(silence_duration_sec * chunks_per_sec)

    while True:
        elapsed = time.time() - start_time
        if elapsed > max_record_sec:
            print("[*] Max recording duration reached.")
            break

        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
        except Exception:
            continue

        frames.append(data)

        audio_data = np.frombuffer(data, dtype=np.int16)
        rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)) if len(audio_data) > 0 else 0

        if rms > silence_threshold:
            speech_started = True
            silent_chunks = 0
        else:
            if speech_started:
                silent_chunks += 1
                if silent_chunks >= max_silent_chunks:
                    print("[*] End of speech detected (silence).")
                    break

    stream.stop_stream()
    stream.close()

    if not frames or not speech_started:
        print("[!] No speech detected after trigger.")
        return None

    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_wav_path = temp_wav.name
    temp_wav.close()

    wf = wave.open(temp_wav_path, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(pa.get_sample_size(FORMAT))
    wf.setframerate(sample_rate)
    wf.writeframes(b''.join(frames))
    wf.close()

    return temp_wav_path


def transcribe_audio(whisper_model: WhisperModel, wav_path: str) -> str:
    """Transcribes local WAV audio file to text using faster-whisper."""
    print("[*] Transcribing speech offline with faster-whisper...")
    try:
        segments, info = whisper_model.transcribe(wav_path, beam_size=5, language="en")
        transcription = " ".join([segment.text for segment in segments]).strip()
        return transcription
    except Exception as e:
        print(f"[!] STT Transcription Error: {e}")
        return ""


async def process_user_query_openai(
    session: ClientSession,
    openai_client: Any,
    openai_tools: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    user_query: str
) -> str:
    """Processes user voice query via OpenAI gpt-4o with MCP tool calls, returning assistant response text."""
    messages.append({"role": "user", "content": user_query})

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
                return "OpenAI API quota exceeded. Please check your account billing or API key."
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
            return response_message.content or "Task completed."


async def process_user_query_gemini(
    session: ClientSession,
    chat: Any,
    user_query: str,
    types_module: Any
) -> str:
    """Processes user voice query via Gemini API with MCP tool calls, returning assistant response text."""
    try:
        response = chat.send_message(user_query)
    except Exception as err:
        err_str = str(err)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            return "Gemini API rate limit or quota exceeded. Please wait a minute or try updating your API key."
        return f"Gemini API Error: {err_str}"

    while hasattr(response, "function_calls") and response.function_calls:
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

            function_response_part = types_module.Part.from_function_response(
                name=tool_name,
                response={"result": result_text}
            )
            response = chat.send_message(function_response_part)

    return response.text or "Task completed."


async def run_ultron_voice_client():
    picovoice_key = os.getenv("PICOVOICE_ACCESS_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if not openai_key and not gemini_key:
        print("[!] ERROR: No LLM API key found.")
        print("    Please set OPENAI_API_KEY or GEMINI_API_KEY in your .env file.")
        sys.exit(1)

    print("===============================================================")
    print("      ULTRON Voice Assistant & Second Brain (MCP Client)")
    print("===============================================================")

    pa = pyaudio.PyAudio()

    porcupine = None
    if picovoice_key:
        try:
            porcupine = init_porcupine(picovoice_key)
            print("[*] Picovoice Porcupine wake word engine loaded.")
        except Exception as e:
            print(f"[!] Warning: Could not initialize Picovoice Porcupine ({e}). Switching to manual trigger mode.")
    else:
        print("[!] NOTICE: PICOVOICE_ACCESS_KEY is not set in .env.")
        print("    - For hands-free 'Ultron' wake word listening, get a free key at https://console.picovoice.ai/ and add PICOVOICE_ACCESS_KEY to .env")
        print("    - Running in Manual Voice Trigger Mode (Press ENTER to speak).")

    print("[*] Loading faster-whisper STT model ('tiny.en')...")
    try:
        whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    except Exception as e:
        print(f"[!] Failed to load faster-whisper model: {e}")
        if porcupine:
            porcupine.delete()
        pa.terminate()
        sys.exit(1)

    server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script],
        env=os.environ.copy()
    )

    print("[*] Launching Local OS & Brain MCP Server...")

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                print("[*] Initializing MCP Session...")
                await session.initialize()

                mcp_tools_res = await session.list_tools()
                available_tools = mcp_tools_res.tools

                print(f"[+] Connected! {len(available_tools)} tools registered:")
                for tool in available_tools:
                    print(f"    - {tool.name}")

                print("\n[*] Hydrating Memory Context from 'brain.db'...")
                initial_memories = "No stored memories found."
                try:
                    memory_result = await session.call_tool("search_memory", {"query": ""})
                    if memory_result.content:
                        initial_memories = memory_result.content[0].text
                except Exception as mem_err:
                    print(f"[!] Memory hydration warning: {mem_err}")

                system_prompt = (
                    "You are ULTRON, a direct, highly intelligent, authoritative, concise, and voice-optimized "
                    "Autonomous Local OS Assistant & Second Brain.\n"
                    "You run locally on the user's computer and execute local system actions safely using MCP tools.\n"
                    "You remember cross-session context stored in SQLite memory.\n\n"
                    "SPOKEN VOICE DIALOGUE RULES:\n"
                    "1. Keep responses clear, direct, and concise (1-3 sentences) since your response will be read aloud via TTS.\n"
                    "2. Avoid verbose markdown formatting, code blocks, or bullet lists in spoken answers unless specifically requested.\n"
                    "3. Use local MCP system tools whenever action is requested.\n"
                    "4. Maintain an intelligent, efficient, and confident persona as ULTRON.\n\n"
                    f"CURRENT STORED MEMORY HYDRATION:\n{initial_memories}"
                )

                openai_client = None
                openai_tools = None
                openai_messages = None
                gemini_chat = None
                gemini_types = None

                if openai_key:
                    from openai import OpenAI
                    openai_client = OpenAI(api_key=openai_key)
                    openai_tools = [convert_mcp_tool_to_openai(t) for t in available_tools]
                    openai_messages = [{"role": "system", "content": system_prompt}]
                    print("\n[*] Active Model: OpenAI (gpt-4o)")
                elif gemini_key:
                    from google import genai
                    from google.genai import types
                    gemini_types = types
                    gemini_client = genai.Client(api_key=gemini_key)
                    func_declarations = [convert_mcp_tool_to_gemini(t, types) for t in available_tools]
                    tools = [types.Tool(function_declarations=func_declarations)]
                    config = types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        tools=tools
                    )
                    gemini_chat = gemini_client.chats.create(model="gemini-3.6-flash", config=config)
                    print("\n[*] Active Model: Google Gemini API (gemini-3.6-flash)")

                print("\n[>>>] ULTRON Voice Assistant ACTIVE.")
                if porcupine:
                    print("      Say 'Ultron' to wake up the assistant. Press Ctrl+C to exit.\n")
                else:
                    print("      Press ENTER in terminal to speak to ULTRON. Press Ctrl+C to exit.\n")

                porcupine_stream = None
                if porcupine:
                    try:
                        porcupine_stream = pa.open(
                            rate=porcupine.sample_rate,
                            channels=1,
                            format=pyaudio.paInt16,
                            input=True,
                            frames_per_buffer=porcupine.frame_length
                        )
                    except Exception as stream_err:
                        print(f"[!] Mic input stream error for Porcupine: {stream_err}. Falling back to manual mode.")
                        porcupine = None

                while True:
                    try:
                        triggered = False

                        if porcupine and porcupine_stream:
                            pcm = porcupine_stream.read(porcupine.frame_length, exception_on_overflow=False)
                            pcm_unpacked = struct.unpack_from("h" * porcupine.frame_length, pcm)
                            keyword_index = porcupine.process(pcm_unpacked)
                            if keyword_index >= 0:
                                print("\n[!] WAKE WORD DETECTED! 'ULTRON' IS LISTENING...")
                                porcupine_stream.stop_stream()
                                triggered = True
                        else:
                            # Manual ENTER key trigger
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(None, input, "\n[Press ENTER to activate ULTRON voice prompt] > ")
                            triggered = True

                        if triggered:
                            await play_wake_cue()

                            wav_file = record_speech_until_silence(pa, sample_rate=16000)

                            if wav_file and os.path.exists(wav_file):
                                user_text = transcribe_audio(whisper_model, wav_file)

                                try:
                                    os.remove(wav_file)
                                except Exception:
                                    pass

                                if user_text:
                                    print(f"\nYou > {user_text}")

                                    if openai_client:
                                        assistant_reply = await process_user_query_openai(
                                            session, openai_client, openai_tools, openai_messages, user_text
                                        )
                                    else:
                                        assistant_reply = await process_user_query_gemini(
                                            session, gemini_chat, user_text, gemini_types
                                        )

                                    print(f"\nUltron > {assistant_reply}\n")

                                    print("[*] Speaking response...")
                                    await speak_text(assistant_reply)

                            if porcupine_stream:
                                porcupine_stream.start_stream()

                        await asyncio.sleep(0.01)

                    except KeyboardInterrupt:
                        print("\n\n[!] Stopping ULTRON Voice Assistant. Goodbye!")
                        break
                    except Exception as loop_err:
                        print(f"[!] Error in voice loop: {loop_err}")
                        await asyncio.sleep(0.5)

                if porcupine_stream:
                    try:
                        porcupine_stream.stop_stream()
                        porcupine_stream.close()
                    except Exception:
                        pass

    except Exception as e:
        print(f"[!] ULTRON Client Communication Error: {e}")
    finally:
        if porcupine:
            try:
                porcupine.delete()
            except Exception:
                pass
        try:
            pa.terminate()
        except Exception:
            pass


def main():
    try:
        asyncio.run(run_ultron_voice_client())
    except KeyboardInterrupt:
        print("\nExiting ULTRON. Goodbye!")


if __name__ == "__main__":
    main()
