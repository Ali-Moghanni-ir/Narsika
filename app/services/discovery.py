import socket
from concurrent.futures import ThreadPoolExecutor,as_completed
from ..models import db,DiscoveryScan,DiscoveryCandidate,OperationRun,now
from ..security import network

def probe(ip,port):
    try:
        with socket.create_connection((ip,port),timeout=.7) as sock:
            sock.settimeout(.7)
            try:banner=sock.recv(256).decode('ascii',errors='ignore').lower()
            except (socket.timeout,OSError):banner=''
            if not banner.startswith('ssh-'):return None
            return 'mikrotik' if 'mikrotik' in banner else 'cisco' if 'cisco' in banner else 'unknown'
    except OSError:return None

def scan(run,parameters):
    item=DiscoveryScan.query.filter_by(run_id=run.id).one()
    hosts=[str(ip) for ip in network(item.cidr).hosts()]
    completed=0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures={pool.submit(probe,ip,item.ssh_port):ip for ip in hosts}
        for future in as_completed(futures):
            db.session.refresh(run)
            if run.cancel_requested:
                for pending in futures:pending.cancel()
                break
            completed+=1;hint=future.result()
            if hint is not None:db.session.add(DiscoveryCandidate(scan_id=item.id,ip_address=futures[future],vendor_hint=hint))
            run.progress=int(completed/max(len(hosts),1)*100)
            db.session.commit()
    return f'Probed {completed} addresses; found {DiscoveryCandidate.query.filter_by(scan_id=item.id).count()} SSH candidates. Vendor hints require verification.'
