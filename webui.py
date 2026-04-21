from flask import Flask, render_template, request, jsonify
import json

app = Flask(__name__)
CONFIG = "config.json"

def load():
    with open(CONFIG) as f:
        return json.load(f)

def save(cfg):
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html", cfg=load())

@app.route("/update", methods=["POST"])
def update():
    cfg = load()
    data = request.json

    for k in data:
        cfg[k] = data[k]

    save(cfg)
    return "OK"

@app.route("/debug")
def debug():
    return open("/tmp/ai_debug.jpg","rb").read()

app.run(host="0.0.0.0", port=5000)
