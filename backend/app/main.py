from fastapi import FastAPI

app = FastAPI(title="Atelic x Dell x NVIDIA — Methane Incident Intelligence")


@app.get("/health")
def health():
    return {"status": "ok"}
