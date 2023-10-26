# SDN-ryu-assignment
## Steps to run
1) cleanup
   ``` sh
    sudo mn -c
   ```
2) run the 210010022_controller.py file using the following command

    ``` sh
     ryu-manager --observe-links 210010022_controller.py
     ```
 
3) connect to mininet

    ``` sh
    sudo mn --topo linear,3 --controller=remote --switch=ovs
    ```
4) dump the flows in all the switches (in the mininet terminal)
   ``` sh
   dpctl dump-flows
   ```
 
