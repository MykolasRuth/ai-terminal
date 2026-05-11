import requests
import json
import time
import subprocess

OLLAMA_URL = "http://localhost:11434/api/chat"

def ask(model, prompt):
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": True
    }

    start = time.time()
    response_text = ""

    with requests.post(OLLAMA_URL, json=payload, stream=True) as r:
        r.raise_for_status()

        final_stats = None

        for line in r.iter_lines():
            if not line:
                continue

            data = json.loads(line.decode("utf-8"))

            if "message" in data and "content" in data["message"]:
                print(data["message"]["content"], end="", flush=True)
                response_text += data["message"]["content"]

            if data.get("done"):
                final_stats = data

        end = time.time()

    print("\n\n--- Stats ---")

    if final_stats:
        prompt_tokens = final_stats.get("prompt_eval_count", 0)
        output_tokens = final_stats.get("eval_count", 0)
        eval_duration_ns = final_stats.get("eval_duration", 0)

        eval_seconds = eval_duration_ns / 1_000_000_000 if eval_duration_ns else 0
        tok_per_sec = output_tokens / eval_seconds if eval_seconds > 0 else 0

        print(f"| Input tokens: {prompt_tokens} | Output tokens: {output_tokens} | Output speed: {tok_per_sec:.2f} tokens/sec | Wall time: {end - start:.2f} sec |")

def list_models():
    response = requests.get("http://localhost:11434/api/tags")
    if response.status_code == 200:
        data = response.json()
        for model in data["models"]:
            print(f"- {model['name']}")
    else:
        print("Failed to fetch models.")

def main():
    list_models()
    try: 
        model = input("Model name: ").strip()

        while True:
            prompt = input("\nYou: ")

            if prompt.lower() in ["exit", "quit", "/bye"]:
                break
            
            if prompt.lower() in ["/model", "/models"]:
                list_models()
                new_model = input("New model name: ").strip()

                if new_model:
                    model = new_model
                    print(f"Switched to {model}")

                continue

            print(f"\n{model}: ", end="")
            ask(model, prompt)
    except KeyboardInterrupt:
        print("\nExiting...")
if __name__ == "__main__":
    main()