import { WebApp } from '@twa-dev/sdk';

// Уведомляем Telegram, что приложение готово
WebApp.ready();

// Раскрываем на весь экран
WebApp.expand();

// Можно вывести в консоль данные о среде
console.log('Telegram WebApp:', {
  platform: WebApp.platform,
  version: WebApp.version,
  theme: WebApp.themeParams,
});

export default WebApp;
