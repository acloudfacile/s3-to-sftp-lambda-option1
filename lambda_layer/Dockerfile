FROM public.ecr.aws/lambda/python:3.11

# Install zip and required packages into /opt/python
RUN yum install -y zip && \
    pip3 install paramiko watchtower -t /opt/python

CMD ["bash"]