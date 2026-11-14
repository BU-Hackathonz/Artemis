import { createRoot } from 'react-dom/client';
import {Companion} from './Companion';
import { App } from './App';
import './styles.css';
createRoot(document.getElementById('root')!).render(window.location.hash === '#companion' ? <Companion /> : <App />);
