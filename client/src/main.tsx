import { createRoot } from 'react-dom/client';
import { App } from './App';
import { AgentWindow } from './AgentWindow';
import './styles.css';
// index.html#agent is the desktop agent window (the ring in a corner of the screen).
createRoot(document.getElementById('root')!).render(window.location.hash === '#agent' ? <AgentWindow /> : <App />);
