import express from "express";
import cors from "cors";

const app = express();
app.use(cors()); // allow frontend access

app.get("/api/key", (req, res) => {
  const apiKey = process.env.api;

  res.json({
    key: apiKey
  });
});

const PORT = process.env.PORT || 5000;
app.listen(PORT, () => {
  console.log("Server running on port", PORT);
});
