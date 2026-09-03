// Model selection + per-chat/global persistence, extracted from useAppState.
function useModelSelection(chatId) {
  const _VALID_MODELS = new Set([..._NRP_MODELS, ..._CLAUDE_MODELS].map(m => m[0]));
  const [selectedModel, setSelectedModelState] = useState(() => {
    try {
      const saved = localStorage.getItem('llm:model');
      return (saved && _VALID_MODELS.has(saved)) ? saved : 'minimax-m2';
    } catch (_) { return 'minimax-m2'; }
  });
  const setSelectedModel = (value) => {
    setSelectedModelState(value);
    try {
      if (chatId) localStorage.setItem(`model:chat:${chatId}`, value);
      localStorage.setItem('llm:model', value);
    } catch (_) {}
  };
  const [showModelPicker, setShowModelPicker] = useState(false);

  useEffect(() => {
    try {
      const perChat = chatId ? localStorage.getItem(`model:chat:${chatId}`) : null;
      const global  = localStorage.getItem('llm:model') || 'minimax-m2';
      const effective = (perChat && _VALID_MODELS.has(perChat)) ? perChat
                      : (_VALID_MODELS.has(global) ? global : 'minimax-m2');
      setSelectedModelState(effective);
    } catch (_) {}
  }, [chatId]);

  return { selectedModel, setSelectedModel, showModelPicker, setShowModelPicker };
}
