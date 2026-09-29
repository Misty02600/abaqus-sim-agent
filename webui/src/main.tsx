import { createRoot } from "react-dom/client";
import App from "./App";
import "./style.css";
import { initializeAppearance } from "./appearance";
initializeAppearance();
createRoot(document.getElementById("root")!).render(<App />);
