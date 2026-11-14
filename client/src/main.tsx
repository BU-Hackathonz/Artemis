import { createRoot } from 'react-dom/client';
import {Companion} from './Companion';
import {FlowWindow} from './FlowWindow';
import { App } from './App';
import './styles.css';
const hash = window.location.hash;
createRoot(document.getElementById('root')!).render(hash === '#companion' ? <Companion /> : hash === '#flow' ? <FlowWindow /> : <App />);
