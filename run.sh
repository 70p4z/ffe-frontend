#!/bin/bash
DIR=`dirname $(readlink -f $0)`
cd $DIR
while [ true ]
do 
	python server.py 
	sleep 1 
done
