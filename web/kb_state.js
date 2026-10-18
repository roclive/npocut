export function mergeOutputs(...groups) {
  return [...new Set(groups.flat().filter(path => typeof path === 'string' && path))];
}

export function hasRecoveredResult(error, autogen) {
  return error?.message === 'Unknown job' &&
    autogen?.status === 'done' &&
    mergeOutputs(autogen.outputs || []).length > 0;
}

export function createMonitorRegistry(run) {
  const active = new Map();

  return {
    start(key, id, view) {
      const current = active.get(key);
      if (current?.id === id) {
        current.view = view;
        return current.promise;
      }
      if (current) current.cancelled = true;

      const entry = { id, view, cancelled: false };
      active.set(key, entry);
      entry.promise = Promise.resolve()
        .then(() => run(id, () => entry.view, () => entry.cancelled, key))
        .finally(() => {
          if (active.get(key) === entry) active.delete(key);
        });
      return entry.promise;
    },
    get(key) { return active.get(key); },
  };
}

export const IMAGE_CONFIG_GUIDANCE = `图片后端配置说明：
• 默认 KB_IMAGE_PROVIDER=gradient，可完全离线运行。
• AI 生图设为 KB_IMAGE_PROVIDER=openai，并在启动 npocut 的环境中提供 OPENAI_API_KEY。
• OpenAI 模式还需安装 requirements-images.txt（Pillow）。
• 密钥只由服务端读取，页面不会保存或显示密钥。
• “就绪”是本地 doctor 检查，不验证网络、账单或模型权限。`;
