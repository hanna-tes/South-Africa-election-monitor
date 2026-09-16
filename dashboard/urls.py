from django.urls import path
from . import views

urlpatterns = [
    # === MAIN DASHBOARD VIEWS ===
    path('', views.HomeView.as_view(), name='home'),
    path('narratives/', views.NarrativesView.as_view(), name='narratives'),
    path('lexicons/', views.LexiconsView.as_view(), name='lexicons'),
    path('peps/', views.PEPsView.as_view(), name='peps'),          # Main PEPs tab
    path('networks/', views.NetworksView.as_view(), name='networks'),
    path('lexicon-management/', views.LexiconManagementView.as_view(), name='lexicon_management'),
    #path('investigative-reports/', views.InvestigativeReportsView.as_view(), name='investigative_reports'),
    path('reports/', views.reports_landing, name='reports_landing'),
    path('reports/<int:report_id>/', views.report_detail, name='report_detail'),
    path('peps/analysis/', views.PEPsAnalysisView.as_view(), name='peps_analysis'),
    path('export-network-csv/', views.export_network_csv, name='export_network_csv'),
    path('export-network-nodes/', views.export_network_nodes_csv, name='export_network_nodes'),
    path('export-network-edges-tweets/', views.export_network_edges_with_tweets, name='export_network_edges_tweets'),
    #path('export-merged-gephi/', views.export_merged_gephi_csv, name='export_merged_gephi_csv'),
    path('networks/export/gephi-edges/', views.export_gephi_edges_csv, name='export_gephi_edges_csv'),
    path('api/trigger-llm-scan/', views.trigger_llm_scan_api, name='trigger_llm_scan'),
    path('export-new-terms-csv/', views.export_new_terms_csv, name='export_new_terms_csv'),
    path('networks/export/complete-network/', views.export_complete_network_csv, name='export_complete_network'),

    # AFRO-XLMR specific endpoints
    path('api/scan-text-afro-xlmr/', views.scan_text_afro_xlmr_api, name='scan_text_afro_xlmr'),
     
    
    # === PEPs DATA VIEWS (FIXED CONFLICT) ===
    path('peps/registry/', views.PEPsHubView.as_view(), name='peps_hub'),   # File cards (HoPR, RC, Executive)
    path('peps/data/', views.PEPsDataView.as_view(), name='peps_data'),     # Spreadsheet view
    
    # === UPLOAD & DATA MANAGEMENT ===
    path('upload/', views.UploadDataView.as_view(), name='upload_data'),
    path('upload/process/', views.ProcessUploadView.as_view(), name='process_upload'),
    path('upload/clear/', views.ClearDataView.as_view(), name='clear_data'),
    
    # === API ENDPOINTS ===
    path('api/scan-text/', views.scan_text_api, name='scan_text_api'),
    path('api/export-posts/', views.export_posts_api, name='export_posts_api'),
    #path('api/network-graph/', views.generate_network_graph, name='generate_network_graph'),
#     path('api/ttp-proxy/v1/chat/completions', views.TTPProxyView.as_view(), name='ttp_proxy_api'),
    path('api/ttp-radar-data/', views.ttp_radar_data_api, name='ttp_radar_data_api'),

    path('api/network-graph/', views.generate_network_graph, name='generate_network_graph'),
    #path('api/ttp-proxy/v1/chat/completions', views.TTPProxyView.as_view(), name='ttp_proxy_api'),

    #path('api/ttp-radar-data/', views.ttp_radar_data_api, name='ttp_radar_data_api'),

    path('api/ttp-radar-data/', views.ttp_radar_data_api, name='ttp_radar_data_api'),

    # Gephi CSV Exports
    path('networks/export-gephi-nodes/', views.export_gephi_nodes_csv, name='export_gephi_nodes'),
    path('networks/export-gephi-edges/', views.export_gephi_edges_csv, name='export_gephi_edges'),
    path('networks/export-gephi-edges-tweets/', views.export_gephi_edges_tweets_csv, name='export_gephi_edges_tweets'),

]
