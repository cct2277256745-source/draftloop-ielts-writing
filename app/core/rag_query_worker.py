"""Private local query encoder process. Never reads corpus or emits query text."""
import json
import sys

from .rag_local_runtime import InProcessQwenEncoder


def main():
    encoder = InProcessQwenEncoder(sys.argv[1] or None)
    print(json.dumps({"ready": True, "pin": encoder.pin.content()}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            query = request["query"]
            if not isinstance(query, str) or not query.strip() or len(query) > 2400:
                raise ValueError("Invalid query.")
            vector = encoder.encode(query)
            print(json.dumps({"vector": vector.tolist()}), flush=True)
        except Exception:
            print(json.dumps({"error": "QUERY_ENCODING_FAILED"}), flush=True)


if __name__ == "__main__":
    main()
