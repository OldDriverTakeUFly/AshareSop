import { createApp } from './createApp.js';

const app = createApp();
const port = Number(process.env.PORT ?? 3000);

app.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`CodeAgent Dashboard API listening on port ${port}`);
});
