$body = @{
  model = "glm-5.3-flash"
  input = "Use the note tool with text equal to hello."
  tools = @(
    @{
      type = "function"
      name = "note"
      description = "Record a short note."
      parameters = @{
        type = "object"
        properties = @{
          text = @{ type = "string" }
        }
        required = @("text")
      }
    }
  )
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8787/v1/responses" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
