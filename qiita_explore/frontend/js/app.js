function App() {
  const state = useAppState();
  return renderApp(state);
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
