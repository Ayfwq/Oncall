import { createRouter, createWebHistory } from 'vue-router'
import ChatView from './views/ChatView.vue'
import IncidentsView from './views/IncidentsView.vue'
import IncidentDetailView from './views/IncidentDetailView.vue'
import ProjectsView from './views/ProjectsView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import KnowledgeView from './views/KnowledgeView.vue'
import SettingsView from './views/SettingsView.vue'
import LoginView from './views/LoginView.vue'
export default createRouter({history:createWebHistory(),routes:[
{path:'/login',component:LoginView},{path:'/',component:ChatView},{path:'/incidents',component:IncidentsView},{path:'/incidents/:id',component:IncidentDetailView},{path:'/projects',component:ProjectsView},{path:'/projects/:id',component:ProjectDetailView},{path:'/knowledge',component:KnowledgeView},{path:'/settings',component:SettingsView}
]})
