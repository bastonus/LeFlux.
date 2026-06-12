import json

transcript_path = "/home/azandikka/.gemini/antigravity-ide/brain/1c02d441-cd67-45ab-a648-ecb7346b6eb1/.system_generated/logs/transcript.jsonl"

with open(transcript_path, "r") as f:
    for line in f:
        try:
            data = json.loads(line)
            step = data.get("step_index")
            if 446 <= step <= 491:
                print(f"--- STEP {step} ({data.get('source')} - {data.get('type')}) ---")
                print(data.get("content")[:600])
                print("-" * 60)
        except Exception as e:
            pass
