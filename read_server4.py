with open("server.py", "r", encoding="utf-8") as f:
    text = f.read()

start = text.find("class DeleteResultsRequest(BaseModel):")
if start != -1:
    print(text[start : start + 1500].encode("ascii", "ignore").decode())
else:
    print("Not found")
