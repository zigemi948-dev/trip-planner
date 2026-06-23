import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './views/PlannerWorkspace.vue';
import './styles.css';

// The app currently mounts the planner workspace directly; router can be added
// when additional pages appear.
createApp(App).use(createPinia()).mount('#app');
