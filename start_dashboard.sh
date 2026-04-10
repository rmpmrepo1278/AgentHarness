  #!/bin/bash
  cd /home/rohit/agentharness                                                                                                                                                                 
  source venv/bin/activate                                  
  python3 -c "from core.observe.dashboard import create_app; app = create_app(data_dir='/home/rohit/agentharness/data'); import uvicorn; uvicorn.run(app, host='0.0.0.0', port=9100)"         
  EOF                                                                                                                                                                                         
  chmod +x ~/agentharness/start_dashboard.sh      
