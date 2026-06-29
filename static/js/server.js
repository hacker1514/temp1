import express from "express";
import dotenv from "dotenv";
import cors from "cors";

dotenv.config();

const app = express();
app.use(cors());

function getGroqKey() {
  return process.env.api || "";
}

app.get("/api/key", (req, res) => {
  res.json({ key: getGroqKey() });
});

app.listen(5000, () => {
  console.log("Server running on port 5000");
});
