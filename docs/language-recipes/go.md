### Go migration (HTTP/SDK → Responses)

- Use `POST /v1/responses`.
- Map `prompt` → `input`, `max_tokens` → `max_output_tokens`.
- Adjust unmarshalling to Responses output shape.

Example (HTTP):

```go
reqBody := strings.NewReader(`{"model":"gpt-5","input":"Hello"}`)
req, _ := http.NewRequest("POST", "https://api.openai.com/v1/responses", reqBody)
req.Header.Set("Authorization", "Bearer "+os.Getenv("OPENAI_API_KEY"))
req.Header.Set("Content-Type", "application/json")
```


