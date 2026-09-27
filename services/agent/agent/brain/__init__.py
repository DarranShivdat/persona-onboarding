"""The brain: pure, deterministic flow engine shared by text and voice.

No network, no LLM, no Pipecat imports in this package. LLM calls happen in
adapters (agent/llm, agent/voice) that hand the brain *validated extractions*.
"""
